"""Fixed production-shift schedule and run-time interval splitting."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import tzinfo as TzInfo


#: Common three-shift roster using local China time.
DEFAULT_TIMEZONE = timezone(timedelta(hours=8))


@dataclass(frozen=True, slots=True)
class Shift:
    """A concrete shift instance."""

    key: str
    label: str
    started_at: datetime
    ended_at: datetime

    def to_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "label": self.label,
            "startedAt": self.started_at.isoformat(),
            "endedAt": self.ended_at.isoformat(),
        }


class ShiftSchedule:
    """Split time against a fixed daily list of shift start times."""

    def __init__(
        self,
        starts: tuple[time, ...] = (time(0), time(8), time(16)),
        labels: tuple[str, ...] = ("A", "B", "C"),
        timezone: "TzInfo | None" = DEFAULT_TIMEZONE,
    ) -> None:
        if not starts:
            raise ValueError("a shift schedule requires at least one start")
        ordered = tuple(sorted(starts))
        if len(set(ordered)) != len(ordered):
            raise ValueError("shift starts must be unique")
        if len(labels) != len(ordered):
            raise ValueError("one label is required for each shift")
        self._starts = ordered
        self._labels = labels
        self._tz = timezone

    @property
    def timezone(self) -> "TzInfo":
        return self._tz or timezone.utc

    def shift_at(self, moment: datetime) -> Shift:
        """Return the concrete shift containing ``moment``."""

        moment = self._as_aware(moment)
        local = moment.astimezone(self.timezone)
        local_time = local.timetz().replace(tzinfo=None)
        day = local.date()
        index = 0
        for candidate, start_time in enumerate(self._starts):
            if local_time >= start_time:
                index = candidate

        start_date = day
        if index == 0 and local_time < self._starts[0]:
            start_date -= timedelta(days=1)
            index = len(self._starts) - 1

        started_local = datetime.combine(start_date, self._starts[index])
        started_at = started_local.replace(tzinfo=self.timezone).astimezone(timezone.utc)
        next_index = (index + 1) % len(self._starts)
        next_date = start_date + timedelta(days=1 if next_index == 0 else 0)
        ended_local = datetime.combine(next_date, self._starts[next_index])
        ended_at = ended_local.replace(tzinfo=self.timezone).astimezone(timezone.utc)
        key = f"{start_date.isoformat()}-{self._labels[index]}"
        return Shift(
            key=key,
            label=self._labels[index],
            started_at=started_at,
            ended_at=ended_at,
        )

    def split_run(
        self, started_at: datetime, ended_at: datetime
    ) -> list[tuple[Shift, float]]:
        """Allocate an elapsed interval to every shift it overlaps."""

        started_at = self._as_aware(started_at)
        ended_at = self._as_aware(ended_at)
        if ended_at < started_at:
            raise ValueError("run end is before its start")

        pieces: list[tuple[Shift, float]] = []
        cursor = started_at
        while cursor < ended_at:
            shift = self.shift_at(cursor)
            boundary = min(shift.ended_at, ended_at)
            seconds = (boundary - cursor).total_seconds()
            if seconds > 0:
                if pieces and pieces[-1][0].key == shift.key:
                    previous, previous_seconds = pieces[-1]
                    pieces[-1] = (previous, previous_seconds + seconds)
                else:
                    pieces.append((shift, seconds))
            cursor = boundary
        return pieces

    def _as_aware(self, moment: datetime) -> datetime:
        if moment.tzinfo is None:
            return moment.replace(tzinfo=self.timezone)
        return moment.astimezone(timezone.utc)
