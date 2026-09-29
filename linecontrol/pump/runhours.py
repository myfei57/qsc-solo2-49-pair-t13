"""Per-shift pump run time ledger with crash-safe incremental carry-over.

A run segment is accounted incrementally:

* On every start/resume the open segment (its start time plus a settlement
  watermark) is persisted inside ``pump.state``, so a restart can never lose
  the segment anchor.
* :meth:`ShiftRunOps.settle` accounts the span between the watermark and a
  given time, splits it at shift boundaries and advances the watermark in the
  same atomic snapshot write. Repeating it is a no-op: time is never counted
  twice.
* On a hard crash only the slice between the last persisted watermark and the
  crash is unaccounted (bounded by the tick interval). A graceful stop/restart
  settles through "now" and loses nothing.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta

from ..domain.records import PumpSnapshot, utcnow
from ..store.keys import KEY_PUMP
from ..store.snapshot import load_json, save_json
from .schedule import LEGACY_KEY, ShiftSchedule


class ShiftRunOps:
    """Mixed into :class:`~linecontrol.pump.pump.Pump`."""

    def __init_ledger__(
        self,
        schedule: ShiftSchedule | None = None,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._schedule = schedule or ShiftSchedule()
        self._clock = clock

    def running(self) -> bool:
        return self.data.running()

    def started_at(self) -> datetime | None:
        return self.data.segment_start

    # -- settlement -----------------------------------------------------

    def settle(self, now: datetime | None = None) -> None:
        """Account the open segment up to ``now`` and advance the watermark."""

        moment = now or self._clock()
        with self.data.lock:
            self._settle_locked(moment)

    def _settle_locked(self, moment: datetime) -> None:
        """Account up to ``moment``; caller holds ``data.lock``."""

        start = self.data.segment_start
        if start is None or self.data.awaiting_resume:
            return
        mark = self.data.settled_until
        if moment <= (mark or start):
            return
        mark = mark or start
        for shift_key, part_start, part_end in self._schedule.split(mark, moment):
            self.data.totals[shift_key] = (
                self.data.totals.get(shift_key, 0.0)
                + (part_end - part_start).total_seconds()
            )
        self.data.settled_until = moment
        self._save_state_locked()

    def _open_segment(self, at: datetime) -> None:
        """Persist a fresh run segment anchored at ``at``."""

        with self.data.lock:
            self.data.segment_start = at
            self.data.settled_until = at
            self.data.awaiting_resume = False
            self._save_state_locked()

    def _resume_or_open_segment(self, at: datetime) -> None:
        """Continue a recovered frozen segment, or open a new one.

        The tail between the last persisted watermark and ``at`` is left
        unaccounted: the pump was not proven running during that gap.
        """

        with self.data.lock:
            start = self.data.segment_start
            if self.data.awaiting_resume and start is not None:
                self.data.settled_until = at
                self.data.awaiting_resume = False
                self._save_state_locked()
            else:
                self.data.segment_start = at
                self.data.settled_until = at
                self.data.awaiting_resume = False
                self._save_state_locked()

    def _close_segment(self, at: datetime | None = None) -> None:
        """Settle through ``at`` and close the open run segment."""

        moment = at or self._clock()
        with self.data.lock:
            if self.data.segment_start is not None:
                self._settle_locked(moment)
            self.data.segment_start = None
            self.data.settled_until = None
            self.data.awaiting_resume = False
            self._save_state_locked()

    # -- queries --------------------------------------------------------

    def _unsettled(self, moment: datetime) -> list[tuple[str, datetime, datetime]]:
        start = self.data.segment_start
        if start is None or self.data.awaiting_resume:
            return []
        mark = self.data.settled_until or start
        if moment <= mark:
            return []
        return self._schedule.split(mark, moment)

    def run_seconds(self, now: datetime | None = None) -> float:
        """Total accounted seconds, including the still-open live segment."""

        moment = now or self._clock()
        with self.data.lock:
            settled = sum(self.data.totals.values())
            live = sum(
                (part_end - part_start).total_seconds()
                for _, part_start, part_end in self._unsettled(moment)
            )
        return settled + live

    def shift_seconds(self, key: str, now: datetime | None = None) -> float:
        """Run seconds attributed to one shift instance (live segment incl.)."""

        moment = now or self._clock()
        with self.data.lock:
            total = self.data.totals.get(key, 0.0)
            for shift_key, part_start, part_end in self._unsettled(moment):
                if shift_key == key:
                    total += (part_end - part_start).total_seconds()
        return total

    def current_shift(self, now: datetime | None = None):
        return self._schedule.shift_at(now or self._clock())

    def shift_report(self, limit: int = 3, now: datetime | None = None) -> dict:
        """Totals for the current shift and the most recent ``limit`` shifts."""

        moment = now or self._clock()
        current = self._schedule.shift_at(moment)
        entries: list[dict[str, object]] = []
        shift = current
        for _ in range(max(limit, 1)):
            entries.append(
                {
                    "key": shift.key,
                    "label": shift.label,
                    "start": shift.start.isoformat(),
                    "end": shift.end.isoformat(),
                    "seconds": self.shift_seconds(shift.key, moment),
                }
            )
            # One microsecond before this shift's start belongs to its
            # predecessor; works even when shift lengths differ.
            previous = shift.start - timedelta(microseconds=1)
            shift = self._schedule.shift_at(previous)
        entries.reverse()
        return {
            "current": current.to_dict(),
            "currentSeconds": self.shift_seconds(current.key, moment),
            "totalSeconds": self.run_seconds(moment),
            "shifts": entries,
        }

    # -- persistence ----------------------------------------------------

    def restore(self, snapshot: PumpSnapshot) -> None:
        """Rebuild shift totals and any open segment read back from disk."""

        with self.data.lock:
            self.data.machine.force("running" if snapshot.running else "stopped")
            self.data.totals = dict(snapshot.totals)
            if snapshot.running and snapshot.segment_start is not None:
                self.data.segment_start = snapshot.segment_start
                self.data.settled_until = (
                    snapshot.settled_until
                    if snapshot.settled_until is not None
                    else snapshot.segment_start
                )
                # Frozen until the operator confirms the pump is circulating.
                self.data.awaiting_resume = True
            else:
                self.data.segment_start = None
                self.data.settled_until = None
                self.data.awaiting_resume = False

    def import_legacy_hours(self, seconds: float) -> None:
        """Fold the pre-shift grand total into a separate legacy bucket once."""

        if seconds <= 0.0:
            return
        with self.data.lock:
            if self.data.totals:
                return
            self.data.totals[LEGACY_KEY] = float(seconds)
            self._save_state_locked()

    def persisted(self) -> tuple[PumpSnapshot, bool]:
        return load_json(self.store, KEY_PUMP, PumpSnapshot)

    def _save_state_locked(self) -> None:
        save_json(
            self.store,
            KEY_PUMP,
            PumpSnapshot(
                running=self.data.running(),
                saved=self._clock(),
                totals=dict(self.data.totals),
                segment_start=self.data.segment_start,
                settled_until=self.data.settled_until,
            ),
        )
