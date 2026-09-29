"""Shift schedule used to split pump run time across crew shifts.

Shifts repeat every day at fixed local-of-record (naive) hours. A shift
*instance* is identified by the exact datetime it started, so a run crossing
midnight is still attributed to the shift that owned each instant.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

#: Default crew rotation: night 00:00, morning 08:00, afternoon 16:00.
DEFAULT_STARTS: tuple[int, ...] = (0, 8, 16)

#: Sentinel bucket holding totals migrated from the pre-shift single counter.
LEGACY_KEY = "__legacy__"


@dataclass(frozen=True, slots=True)
class Shift:
    """One shift instance: its label and the half-open time span it owns."""

    key: str
    label: str
    start: datetime
    end: datetime

    def to_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "label": self.label,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
        }


class ShiftSchedule:
    """Daily repeating shift rotation anchored at fixed hours."""

    def __init__(self, starts: tuple[int, ...] = DEFAULT_STARTS) -> None:
        if not starts:
            raise ValueError("shift schedule needs at least one start hour")
        if any(h < 0 or h >= 24 for h in starts):
            raise ValueError("shift start hours must lie within [0, 24)")
        if len(set(starts)) != len(starts):
            raise ValueError("shift start hours must be unique")
        self._hours: tuple[int, ...] = tuple(sorted(starts))
        self._labels: dict[int, str] = {
            hour: f"{index + 1:02d}班" for index, hour in enumerate(self._hours)
        }

    @property
    def hours(self) -> tuple[int, ...]:
        return self._hours

    def label_for(self, hour: int) -> str:
        return self._labels[hour]

    def _shift_starting_at(self, day: datetime, hour: int) -> Shift:
        start = day.replace(hour=hour, minute=0, second=0, microsecond=0)
        return Shift(
            key=start.strftime("%Y-%m-%d %H:%M"),
            label=self._labels[hour],
            start=start,
            end=self._next_start(start),
        )

    def _day_start(self, moment: datetime) -> datetime:
        return moment.replace(hour=0, minute=0, second=0, microsecond=0)

    def _boundaries_on(self, day: datetime) -> list[datetime]:
        base = self._day_start(day)
        return [base + timedelta(hours=h) for h in self._hours]

    def _prev_start(self, moment: datetime) -> datetime:
        """Most recent boundary at or before ``moment``."""

        day = self._day_start(moment)
        for offset_days in (0, -1):
            boundaries = self._boundaries_on(day + timedelta(days=offset_days))
            earlier = [b for b in boundaries if b <= moment]
            if earlier:
                return earlier[-1]
        # Unreachable: the 0-based boundary on a previous day always matches.
        raise RuntimeError("no shift boundary found")

    def _next_start(self, moment: datetime) -> datetime:
        """First boundary strictly after ``moment``."""

        day = self._day_start(moment)
        for offset_days in (0, 1):
            boundaries = self._boundaries_on(day + timedelta(days=offset_days))
            later = [b for b in boundaries if b > moment]
            if later:
                return later[0]
        raise RuntimeError("no shift boundary found")

    def shift_at(self, moment: datetime) -> Shift:
        """Return the shift instance owning ``moment``."""

        start = self._prev_start(moment)
        return Shift(
            key=start.strftime("%Y-%m-%d %H:%M"),
            label=self._labels[start.hour],
            start=start,
            end=self._next_start(start),
        )

    def split(
        self, start: datetime, end: datetime
    ) -> list[tuple[str, datetime, datetime]]:
        """Split ``[start, end)`` into ``(shift_key, part_start, part_end)``.

        Every returned piece lies wholly inside one shift instance, in time
        order, and the pieces concatenate back to the original span.
        """

        if end <= start:
            return []
        pieces: list[tuple[str, datetime, datetime]] = []
        cursor = start
        while cursor < end:
            shift = self.shift_at(cursor)
            part_end = min(shift.end, end)
            pieces.append((shift.key, cursor, part_end))
            cursor = part_end
        return pieces
