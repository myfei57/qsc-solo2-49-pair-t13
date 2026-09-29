"""Pump run-hour accounting split by production shift."""

from __future__ import annotations

import json
from datetime import datetime

from ..domain.records import PumpSnapshot, utcnow
from ..domain.shift import Shift, ShiftSchedule
from ..store.keys import KEY_HOURS, KEY_PUMP
from ..store.snapshot import load_json, save_json, save_float

LEGACY_SHIFT_KEY = "legacy"


class RunHourOps:
    """Mixed into :class:`~linecontrol.pump.pump.Pump`."""

    def started_at(self) -> datetime | None:
        return self.data.started_at

    def current_shift(self) -> Shift:
        return self.shifts.shift_at(utcnow())

    def record_run(self, seconds: float) -> None:
        """Record a completed interval in the shift containing this moment."""

        seconds = max(float(seconds), 0.0)
        shift = self.current_shift()
        self._add_seconds({shift.key: seconds})
        self._persist_run(utcnow())

    def run_seconds(self, at: datetime | None = None) -> float:
        """Total settled seconds, plus the live segment when running."""

        total = sum(self.data.shift_seconds.values())
        if at is None:
            at = utcnow()
        if self.data.started_at is not None:
            total += max((at - self.data.started_at).total_seconds(), 0.0)
        return total

    def settled_run_seconds(self) -> float:
        return sum(self.data.shift_seconds.values())

    def shift_run_seconds(self, at: datetime | None = None) -> dict[str, float]:
        """Settled shift seconds with the currently running segment added."""

        result = dict(self.data.shift_seconds)
        if at is None:
            at = utcnow()
        if self.data.started_at is not None and self.running():
            for shift, seconds in self.shifts.split_run(self.data.started_at, at):
                result[shift.key] = result.get(shift.key, 0.0) + seconds
        return result

    def shift_report(self, at: datetime | None = None) -> dict[str, object]:
        """Projection used at hand-over: every shift and its pump seconds."""

        if at is None:
            at = utcnow()
        seconds = self.shift_run_seconds(at)
        current = self.shifts.shift_at(at)
        return {
            "current": current.to_dict(),
            "currentSeconds": seconds.get(current.key, 0.0),
            "totalSeconds": sum(seconds.values()),
            "shifts": [
                {"key": key, "seconds": value}
                for key, value in sorted(seconds.items())
            ],
        }

    def settle_shift(self, at: datetime | None = None) -> dict[str, object]:
        """Close the running interval up to ``at`` and restart its basis.

        Calling this during hand-over makes the outgoing shift's total durable
        without stopping the physical pump.  The next segment is allocated to
        the shift containing ``at``.
        """

        if at is None:
            at = utcnow()
        self._settle_through(at, persist=True)
        return self.shift_report(at)

    def close_run(self, at: datetime | None = None) -> None:
        """Allocate the current run through ``at`` and clear the run basis."""

        if at is None:
            at = utcnow()
        if self.data.started_at is not None:
            self._settle_through(at, persist=False)
        self.data.started_at = None

    def restore_run(self, seconds: float, *, persist: bool = False) -> None:
        """Accept the old single-counter snapshot as unallocated history."""

        self.data.run_seconds = max(float(seconds), 0.0)
        if seconds and LEGACY_SHIFT_KEY not in self.data.shift_seconds:
            self.data.shift_seconds[LEGACY_SHIFT_KEY] = self.data.run_seconds
        if persist:
            self._persist_run(utcnow())

    def restore_run_state(self, snapshot: PumpSnapshot) -> None:
        """Rebuild run accounting and reconcile a run interrupted by restart."""

        self.data.shift_seconds = dict(snapshot.shift_seconds)
        unallocated = max(
            snapshot.run_seconds - sum(self.data.shift_seconds.values()), 0.0
        )
        if unallocated:
            self.data.shift_seconds[LEGACY_SHIFT_KEY] = (
                self.data.shift_seconds.get(LEGACY_SHIFT_KEY, 0.0) + unallocated
            )
        self.data.run_seconds = sum(self.data.shift_seconds.values())
        self.data.started_at = snapshot.started_at if snapshot.running else None
        if snapshot.running and snapshot.started_at is not None:
            # Recovery is the first trusted point after shutdown.  Settle the
            # complete interrupted segment there, then continue from this point.
            recovered_at = utcnow()
            self._settle_through(recovered_at, persist=True)
            self.data.started_at = recovered_at

    def persisted_hours(self) -> float:
        snapshot, ok = load_json(self.store, KEY_PUMP, PumpSnapshot)
        if ok:
            return snapshot.run_seconds
        return self._load_legacy_hours()

    def _load_legacy_hours(self) -> float:
        raw = self.store.load(KEY_HOURS)
        if raw is None:
            return 0.0
        try:
            return max(float(json.loads(raw.decode("utf-8"))), 0.0)
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            return 0.0

    def _settle_through(self, at: datetime, *, persist: bool) -> None:
        if self.data.started_at is None:
            if persist:
                self._persist_run(at)
            return
        pieces = self.shifts.split_run(self.data.started_at, at)
        if pieces:
            self._add_seconds({shift.key: seconds for shift, seconds in pieces})
        self.data.started_at = at
        if persist:
            self._persist_run(at)

    def _add_seconds(self, pieces: dict[str, float]) -> None:
        for key, seconds in pieces.items():
            if seconds > 0:
                self.data.shift_seconds[key] = (
                    self.data.shift_seconds.get(key, 0.0) + seconds
                )
        self.data.run_seconds = sum(self.data.shift_seconds.values())

    def _persist_run(self, at: datetime) -> None:
        snapshot = PumpSnapshot(
            running=self.running(),
            run_seconds=sum(self.data.shift_seconds.values()),
            started_at=self.data.started_at,
            shift_seconds=dict(self.data.shift_seconds),
            saved=at,
        )
        save_json(self.store, KEY_PUMP, snapshot)
        save_float(self.store, KEY_HOURS, snapshot.run_seconds)

    @property
    def shifts(self) -> ShiftSchedule:
        return self._shifts
