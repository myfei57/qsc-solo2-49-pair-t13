"""Circulation pump gating and per-shift run hour accounting."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from linecontrol.bus import EventBus
from linecontrol.domain.records import PumpSnapshot
from linecontrol.pickle.tank import Tank
from linecontrol.pump.errors import PumpError, PumpErrorCode
from linecontrol.pump.pump import Pump
from linecontrol.pump.schedule import ShiftSchedule
from linecontrol.pump.state import PumpState
from linecontrol.store.filestore import FileStore
from linecontrol.store.keys import KEY_PUMP

UTC = timezone.utc


class StripFlag:
    def __init__(self, stopped: bool) -> None:
        self.stopped = stopped

    def __call__(self) -> bool:
        return self.stopped


class Clock:
    """Deterministic, manually advanced clock."""

    def __init__(self, moment: datetime) -> None:
        self.moment = moment

    def __call__(self) -> datetime:
        return self.moment

    def advance(self, seconds: float) -> None:
        self.moment += timedelta(seconds=seconds)


def build(tmp_path, stopped: bool = True, clock: Clock | None = None) -> Pump:
    store = FileStore(tmp_path)
    bus = EventBus()
    tank = Tank(store, bus)
    return Pump(
        tank,
        store,
        bus,
        StripFlag(stopped),
        schedule=ShiftSchedule(),
        clock=clock,
    )


def at(hour: int, minute: int = 0, day: int = 15) -> datetime:
    return datetime(2026, 9, day, hour, minute, tzinfo=UTC)


def start_at(pump: Pump, clock: Clock, moment: datetime) -> None:
    clock.moment = moment
    pump.tank.level_check(4.0)
    pump.start()


def test_start_refuses_a_low_level(tmp_path) -> None:
    pump = build(tmp_path)
    pump.tank.level_check(1.0)
    with pytest.raises(PumpError) as caught:
        pump.start_gate()
    assert caught.value.code is PumpErrorCode.LEVEL_LOW
    with pytest.raises(PumpError):
        pump.start()
    assert pump.running() is False


def test_start_and_resume_both_gate_on_the_level(tmp_path) -> None:
    pump = build(tmp_path)
    pump.tank.level_check(4.0)
    pump.start()
    assert pump.running() is True
    assert pump.tank.level() == pytest.approx(4.0)
    pump.stop()
    pump.tank.level_check(1.2)
    with pytest.raises(PumpError) as caught:
        pump.resume()
    assert caught.value.code is PumpErrorCode.LEVEL_LOW
    pump.tank.level_check(3.0)
    pump.resume()
    assert pump.running() is True


def test_stop_requires_a_stopped_strip(tmp_path) -> None:
    pump = build(tmp_path, stopped=False)
    pump.tank.level_check(4.0)
    pump.start()
    with pytest.raises(PumpError) as caught:
        pump.stop_gate()
    assert caught.value.code is PumpErrorCode.STRIP_RUN
    snap, ok = pump.persisted()
    assert ok is True and snap.running is True
    assert pump.running() is True


def test_run_hours_accumulate_into_the_current_shift_on_stop(tmp_path) -> None:
    clock = Clock(at(9, 0))
    pump = build(tmp_path, clock=clock)
    start_at(pump, clock, at(9, 0))
    assert pump.started_at() is not None
    clock.advance(600.0)  # 10 minutes, still morning shift
    pump.stop()

    assert pump.started_at() is None
    snap, ok = pump.persisted()
    assert ok is True and snap.running is False
    assert pump.run_seconds() == pytest.approx(600.0)
    report = pump.shift_report()
    assert report["currentSeconds"] == pytest.approx(600.0)
    current_key = report["current"]["key"]
    assert snap.totals[current_key] == pytest.approx(600.0)
    assert pump.data.machine.state == PumpState.STOPPED


def test_a_cross_shift_run_is_split_between_the_two_shifts(tmp_path) -> None:
    clock = Clock(at(7, 50))
    pump = build(tmp_path, clock=clock)
    start_at(pump, clock, at(7, 50))
    clock.advance(600.0)  # 08:00 boundary crossed at the 10-minute mark
    pump.settle()
    clock.advance(600.0)  # another 10 minutes inside the morning shift
    pump.stop()

    night_key = at(0, 0).strftime("%Y-%m-%d %H:%M")
    morning_key = at(8, 0).strftime("%Y-%m-%d %H:%M")
    assert pump.shift_seconds(night_key) == pytest.approx(600.0)
    assert pump.shift_seconds(morning_key) == pytest.approx(600.0)
    assert pump.run_seconds() == pytest.approx(1200.0)

    report = pump.shift_report(limit=3, now=at(8, 10))
    keys = [entry["key"] for entry in report["shifts"]]
    afternoon_prev = datetime(2026, 9, 14, 16, 0, tzinfo=UTC).strftime(
        "%Y-%m-%d %H:%M"
    )
    assert keys == [afternoon_prev, night_key, morning_key]
    assert report["current"]["key"] == morning_key


def test_a_run_crossing_midnight_is_split_into_the_two_adjacent_shifts(
    tmp_path,
) -> None:
    clock = Clock(at(23, 50))
    pump = build(tmp_path, clock=clock)
    start_at(pump, clock, at(23, 50))
    clock.advance(1200.0)  # 20 minutes: 10 before midnight, 10 after
    pump.stop()

    afternoon_key = at(16, 0).strftime("%Y-%m-%d %H:%M")
    night_key = at(0, 0, day=16).strftime("%Y-%m-%d %H:%M")
    assert pump.shift_seconds(afternoon_key) == pytest.approx(600.0)
    assert pump.shift_seconds(night_key) == pytest.approx(600.0)


def test_incremental_settlement_is_idempotent_and_never_double_counts(
    tmp_path,
) -> None:
    clock = Clock(at(10, 0))
    pump = build(tmp_path, clock=clock)
    start_at(pump, clock, at(10, 0))
    clock.advance(30.0)
    pump.settle()
    # Re-settling the same instant must not add anything.
    pump.settle()
    assert pump.run_seconds() == pytest.approx(30.0)
    clock.advance(30.0)
    pump.settle()
    pump.settle(clock.moment - timedelta(seconds=15))  # behind watermark: ignored
    assert pump.run_seconds() == pytest.approx(60.0)
    pump.stop()
    assert pump.run_seconds() == pytest.approx(60.0)


def test_restart_carries_an_open_segment_over_without_loss_or_duplication(
    tmp_path,
) -> None:
    clock = Clock(at(9, 0))
    pump = build(tmp_path, clock=clock)
    start_at(pump, clock, at(9, 0))
    clock.advance(60.0)
    pump.settle()  # heartbeat persisted a 60 s watermark
    clock.advance(15.0)  # unsaved tail before the crash

    # Simulate a hard process restart from the persisted snapshot.
    store = FileStore(tmp_path)
    bus = EventBus()
    reborn = Pump(
        Tank(store, bus),
        store,
        bus,
        StripFlag(True),
        schedule=ShiftSchedule(),
        clock=clock,
    )
    snapshot, ok = reborn.persisted()
    assert ok is True and snapshot.running is True
    reborn.restore(snapshot)
    assert reborn.running() is True
    # While awaiting the operator's resume the segment is frozen: downtime is
    # not extrapolated, and nothing already counted is added again.
    clock.advance(3600.0)
    assert reborn.run_seconds() == pytest.approx(60.0)

    reborn.tank.level_check(4.0)
    reborn.resume()
    clock.advance(120.0)
    reborn.settle()
    # 60 s before crash + 120 s after resume; the 15 s tail and the hour of
    # downtime are neither counted nor duplicated.
    assert reborn.run_seconds() == pytest.approx(180.0)
    morning_key = at(8, 0).strftime("%Y-%m-%d %H:%M")
    assert reborn.shift_seconds(morning_key) == pytest.approx(180.0)


def test_restart_across_a_shift_boundary_keeps_the_old_shift_total(tmp_path) -> None:
    clock = Clock(at(7, 55))
    pump = build(tmp_path, clock=clock)
    start_at(pump, clock, at(7, 55))
    clock.advance(300.0)  # settle 5 night-shift minutes just past 08:00
    pump.settle()

    store = FileStore(tmp_path)
    bus = EventBus()
    reborn = Pump(
        Tank(store, bus),
        store,
        bus,
        StripFlag(True),
        schedule=ShiftSchedule(),
        clock=clock,
    )
    snapshot, _ = reborn.persisted()
    reborn.restore(snapshot)
    reborn.tank.level_check(4.0)
    clock.moment = at(8, 20)
    reborn.resume()
    clock.advance(600.0)
    reborn.stop()

    night_key = at(0, 0).strftime("%Y-%m-%d %H:%M")
    morning_key = at(8, 0).strftime("%Y-%m-%d %H:%M")
    assert reborn.shift_seconds(night_key) == pytest.approx(300.0)
    assert reborn.shift_seconds(morning_key) == pytest.approx(600.0)
    assert reborn.run_seconds() == pytest.approx(900.0)


def test_multiple_runs_in_one_shift_accumulate_in_the_same_bucket(tmp_path) -> None:
    clock = Clock(at(9, 0))
    pump = build(tmp_path, clock=clock)
    start_at(pump, clock, at(9, 0))
    clock.advance(300.0)
    pump.stop()
    clock.moment = at(10, 0)
    pump.start()
    clock.advance(300.0)
    pump.stop()
    morning_key = at(8, 0).strftime("%Y-%m-%d %H:%M")
    assert pump.shift_seconds(morning_key) == pytest.approx(600.0)
    assert pump.run_seconds() == pytest.approx(600.0)


def test_replenish_gate_uses_the_calibrated_concentration(tmp_path) -> None:
    pump = build(tmp_path)
    pump.tank.level_check(4.0)
    pump.tank.calibrate(1.0)
    with pytest.raises(PumpError) as caught:
        pump.replenish_gate(3.0)
    assert caught.value.code is PumpErrorCode.NO_REPLENISH
    pump.replenish_gate(10.0)


def test_state_snapshot_restore_round_trips_totals(tmp_path) -> None:
    pump = build(tmp_path)
    pump.restore(PumpSnapshot(running=False, totals={"2026-09-15 08:00": 3600.0}))
    assert pump.running() is False
    assert pump.run_seconds(now=at(12, 0)) == pytest.approx(3600.0)
    assert pump.store.exists(KEY_PUMP) is False


def test_legacy_grand_total_is_imported_once_into_a_legacy_bucket(tmp_path) -> None:
    pump = build(tmp_path)
    pump.import_legacy_hours(7200.0)
    assert pump.run_seconds(now=at(12, 0)) == pytest.approx(7200.0)
    # A second import must be ignored so the old total is never doubled.
    pump.import_legacy_hours(7200.0)
    assert pump.run_seconds(now=at(12, 0)) == pytest.approx(7200.0)
    snap, ok = pump.persisted()
    assert ok is True


def test_snapshot_view(tmp_path) -> None:
    pump = build(tmp_path)
    pump.tank.level_check(4.0)
    pump.start()
    view = pump.snapshot()
    assert view["running"] is True
    assert view["persisted"] is True
    assert view["startedAt"] is not None
    assert "currentSeconds" in view["shift"]
    assert view["transitions"] == [PumpState.STOPPED, PumpState.RUNNING]
