"""Snapshot recovery on start-up."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from linecontrol.domain.records import PumpSnapshot, RinseSnapshot, TensionSnapshot
from linecontrol.line.machine import Line
from linecontrol.store.keys import KEY_GAIN, KEY_PUMP, KEY_RINSE, KEY_TENSION
from linecontrol.store.snapshot import save_float, save_json

UTC = timezone.utc


def test_recovery_of_an_empty_directory(tmp_path: Path) -> None:
    line = Line(tmp_path)
    state = line.recover()
    assert state.loaded == ()
    assert line.phase.value == "idle"
    assert line.pump.running() is False


def test_recovery_rebuilds_every_controller(tmp_path: Path) -> None:
    line = Line(tmp_path)
    line.recover()
    save_json(line.store, KEY_TENSION, TensionSnapshot(value=91.0, gain=1.15))
    save_float(line.store, KEY_GAIN, 1.15)
    save_json(line.store, KEY_PUMP, PumpSnapshot(running=False))
    save_json(line.store, KEY_RINSE, RinseSnapshot(active=True))
    line.store.save("pump.hours", b"240.0")
    line.store.save("strip.track", b'{"position": 55.0, "seam_id": "s-1"}')
    line.store.save("pickle.acid", b'{"level": 6.5, "offset": 0.75}')
    line.store.save("weld.latch", b'{"interlock": true, "alarm": true}')

    recovered = Line(tmp_path)
    state = recovered.recover()

    assert "tension.current" in state.loaded
    assert recovered.tension.gain() == 1.15
    assert recovered.tension.setpoint() == 91.0
    assert recovered.pump.running() is False
    # The pre-shift grand total is carried over in the legacy bucket.
    assert recovered.pump.run_seconds() == pytest.approx(240.0)
    assert recovered.strip.track_position() == 55.0
    assert recovered.pickle.level() == 6.5
    assert recovered.pickle.offset() == 0.75
    assert recovered.weld.interlock_ok() is False
    assert recovered.pickle.rinsing() is False


def test_recovery_freezes_a_running_pump_segment_until_resume(tmp_path: Path) -> None:
    moments = {"now": datetime(2026, 9, 15, 9, 0, tzinfo=UTC)}

    def clock() -> datetime:
        return moments["now"]

    line = Line(tmp_path, clock=clock)
    line.recover()
    line.pump.tank.level_check(4.0)
    line.pump.start()
    moments["now"] = datetime(2026, 9, 15, 9, 1, tzinfo=UTC)
    line.pump.settle()
    assert line.pump.started_at() is not None

    recovered = Line(tmp_path, clock=clock)
    recovered.recover()

    assert recovered.pump.running() is True
    # Frozen: downtime after the crash is not extrapolated as run time.
    later = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
    assert recovered.pump.run_seconds(now=later) == pytest.approx(60.0)
    # Resuming re-anchors the watermark, and time afterwards counts normally.
    recovered.pump.tank.level_check(4.0)
    moments["now"] = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
    recovered.pump.resume()
    assert recovered.pump.data.awaiting_resume is False
    moments["now"] = datetime(2026, 9, 15, 12, 10, tzinfo=UTC)
    recovered.pump.settle()
    assert recovered.pump.run_seconds(now=moments["now"]) == pytest.approx(660.0)


def test_recovery_stops_a_restored_rinse_and_lifts_rolls(tmp_path: Path) -> None:
    line = Line(tmp_path)
    line.recover()
    save_json(line.store, KEY_RINSE, RinseSnapshot(active=True))

    recovered = Line(tmp_path)
    recovered.recover()

    assert recovered.pickle.rinsing() is False
    assert recovered.exit.roll_up() is True
    assert recovered.exit.exhaust_ok() is False
    persisted, ok = recovered.pickle.rinse_persisted()
    assert ok is True and persisted.active is False


def test_recovery_restores_the_audit_log(tmp_path: Path) -> None:
    line = Line(tmp_path)
    line.recover()
    line.load_coil("C-9", 40.0)
    line.weld_and_speedup()
    line.audit.flush()
    assert len(line.audit.events()) > 0

    recovered = Line(tmp_path)
    recovered.recover()
    assert len(recovered.audit.events()) > 0


def test_recovery_leaves_a_foreign_gain_alone(tmp_path: Path) -> None:
    line = Line(tmp_path)
    line.recover()
    line.store.save(KEY_GAIN, b"0")
    recovered = Line(tmp_path)
    recovered.recover()
    assert recovered.tension.gain() == 1.0
