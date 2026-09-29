"""Shift schedule boundary and split behaviour."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from linecontrol.pump.schedule import ShiftSchedule

UTC = timezone.utc


def dt(hour: int, minute: int = 0, day: int = 15) -> datetime:
    return datetime(2026, 9, day, hour, minute, tzinfo=UTC)


def test_default_schedule_marks_the_three_daily_shifts() -> None:
    schedule = ShiftSchedule()
    assert schedule.hours == (0, 8, 16)
    assert schedule.shift_at(dt(7, 59)).key == dt(0, 0).strftime("%Y-%m-%d %H:%M")
    assert schedule.shift_at(dt(8, 0)).key == dt(8, 0).strftime("%Y-%m-%d %H:%M")
    assert schedule.shift_at(dt(23, 59)).key == dt(16, 0).strftime("%Y-%m-%d %H:%M")
    assert schedule.shift_at(dt(0, 0)).label == "01班"
    assert schedule.shift_at(dt(8, 0)).label == "02班"
    assert schedule.shift_at(dt(16, 0)).label == "03班"


def test_a_shift_instance_owns_its_half_day_span_across_midnight() -> None:
    schedule = ShiftSchedule(starts=(0, 12))
    morning = schedule.shift_at(dt(9))
    assert morning.start == dt(0, 0)
    assert morning.end == dt(12, 0)
    evening = schedule.shift_at(dt(23, 59))
    assert evening.start == dt(12, 0)
    assert evening.end == dt(0, 0, day=16)
    after_midnight = schedule.shift_at(dt(0, 1, day=16))
    assert after_midnight.key == dt(0, 0, day=16).strftime("%Y-%m-%d %H:%M")
    assert after_midnight.end == dt(12, 0, day=16)


def test_split_returns_nothing_for_an_empty_or_reversed_span() -> None:
    schedule = ShiftSchedule()
    assert schedule.split(dt(9), dt(9)) == []
    assert schedule.split(dt(9, 1), dt(9)) == []


def test_split_pieces_concatenate_back_to_the_original_span() -> None:
    schedule = ShiftSchedule()
    start, end = dt(7, 50), dt(16, 30)
    pieces = schedule.split(start, end)
    assert [key for key, _, _ in pieces] == [
        dt(0, 0).strftime("%Y-%m-%d %H:%M"),
        dt(8, 0).strftime("%Y-%m-%d %H:%M"),
        dt(16, 0).strftime("%Y-%m-%d %H:%M"),
    ]
    assert pieces[0][1] == start
    assert pieces[-1][2] == end
    for (_, _, tail), (_, next_head, _) in zip(pieces, pieces[1:]):
        assert tail == next_head
    total = sum((p_end - p_start).total_seconds() for _, p_start, p_end in pieces)
    assert total == pytest.approx((end - start).total_seconds())


def test_split_handles_a_span_crossing_midnight() -> None:
    schedule = ShiftSchedule(starts=(0, 8, 16))
    pieces = schedule.split(dt(23, 50), dt(0, 20, day=16))
    assert len(pieces) == 2
    assert pieces[0][0] == dt(16, 0).strftime("%Y-%m-%d %H:%M")
    assert pieces[1][0] == dt(0, 0, day=16).strftime("%Y-%m-%d %H:%M")
    assert pieces[0][2] == dt(0, 0, day=16)
    assert pieces[1][1] == dt(0, 0, day=16)


def test_non_uniform_shifts_walk_back_to_the_correct_predecessor() -> None:
    # Night shift is 12 hours long (20:00 -> 08:00).
    schedule = ShiftSchedule(starts=(8, 20))
    morning = schedule.shift_at(dt(9))
    night = schedule.shift_at(morning.start - timedelta(microseconds=1))
    assert night.start == dt(20, 0, day=14)
    assert night.end == dt(8, 0)
    previous_afternoon = schedule.shift_at(
        night.start - timedelta(microseconds=1)
    )
    assert previous_afternoon.start == dt(8, 0, day=14)


def test_invalid_schedules_are_rejected() -> None:
    with pytest.raises(ValueError):
        ShiftSchedule(starts=())
    with pytest.raises(ValueError):
        ShiftSchedule(starts=(8, 24))
    with pytest.raises(ValueError):
        ShiftSchedule(starts=(8, 8))
