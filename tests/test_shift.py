"""Shift schedule and cross-shift run allocation."""

from __future__ import annotations

from datetime import datetime, time, timezone

from linecontrol.bus import EventBus
from linecontrol.domain.shift import ShiftSchedule
from linecontrol.pickle.tank import Tank
from linecontrol.pump.pump import Pump
from linecontrol.store.filestore import FileStore


class StripFlag:
    def __call__(self) -> bool:
        return True


UTC_SHIFTS = ShiftSchedule(
    starts=(time(0), time(8), time(16)),
    labels=("A", "B", "C"),
    timezone=timezone.utc,
)


def stamp(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


def build_pump(tmp_path) -> Pump:
    store = FileStore(tmp_path)
    bus = EventBus()
    tank = Tank(store, bus)
    return Pump(tank, store, bus, StripFlag(), UTC_SHIFTS)


def test_interval_is_split_at_shift_boundaries() -> None:
    pieces = UTC_SHIFTS.split_run(stamp("2026-01-01T07:30"), stamp("2026-01-01T10:00"))
    assert [(shift.key, seconds) for shift, seconds in pieces] == [
        ("2026-01-01-A", 1800.0),
        ("2026-01-01-B", 7200.0),
    ]


def test_stop_allocates_the_entire_cross_shift_run(tmp_path) -> None:
    pump = build_pump(tmp_path)
    pump.data.machine.force("running")
    pump.data.started_at = stamp("2026-01-01T07:30")
    pump._persist_run(stamp("2026-01-01T07:30"))

    pump.close_run(stamp("2026-01-01T10:00"))

    assert pump.data.shift_seconds == {
        "2026-01-01-A": 1800.0,
        "2026-01-01-B": 7200.0,
    }
    assert pump.run_seconds(stamp("2026-01-01T10:00")) == 9000.0


def test_handover_settles_without_stopping_or_losing_the_next_segment(tmp_path) -> None:
    pump = build_pump(tmp_path)
    pump.data.machine.force("running")
    pump.data.started_at = stamp("2026-01-01T07:30")
    pump._persist_run(stamp("2026-01-01T07:30"))

    report = pump.settle_shift(stamp("2026-01-01T08:00"))

    assert pump.running() is True
    assert pump.started_at() == stamp("2026-01-01T08:00")
    assert report["shifts"] == [
        {"key": "2026-01-01-A", "seconds": 1800.0},
    ]

    pump.close_run(stamp("2026-01-01T09:00"))
    assert pump.data.shift_seconds == {
        "2026-01-01-A": 1800.0,
        "2026-01-01-B": 3600.0,
    }


def test_recovery_counts_an_interrupted_run_once(tmp_path, monkeypatch) -> None:
    pump = build_pump(tmp_path)
    pump.data.machine.force("running")
    pump.data.started_at = stamp("2026-01-01T07:50")
    pump._persist_run(stamp("2026-01-01T07:50"))

    recovered_at = stamp("2026-01-01T08:10")
    monkeypatch.setattr("linecontrol.pump.runhours.utcnow", lambda: recovered_at)

    recovered = build_pump(tmp_path)
    snapshot, ok = recovered.persisted()
    assert ok is True
    recovered.restore(snapshot)

    assert recovered.data.shift_seconds == {
        "2026-01-01-A": 600.0,
        "2026-01-01-B": 600.0,
    }
    assert recovered.persisted_hours() == 1200.0

    recovered_again = build_pump(tmp_path)
    snapshot, ok = recovered_again.persisted()
    assert ok is True
    recovered_again.restore(snapshot)
    assert recovered_again.data.shift_seconds == {
        "2026-01-01-A": 600.0,
        "2026-01-01-B": 600.0,
    }

    recovered.close_run(stamp("2026-01-01T08:20"))
    assert recovered.data.shift_seconds == {
        "2026-01-01-A": 600.0,
        "2026-01-01-B": 1200.0,
    }
