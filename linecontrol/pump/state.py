"""Pump run state and its transitions."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime

from ..domain.machine import StateMachine


class PumpState:
    STOPPED = "stopped"
    RUNNING = "running"


PUMP_TRANSITIONS: dict[str, set[str]] = {
    PumpState.STOPPED: {PumpState.RUNNING},
    PumpState.RUNNING: {PumpState.STOPPED},
}


def new_pump_machine() -> StateMachine:
    return StateMachine("pump", PumpState.STOPPED, PUMP_TRANSITIONS)


@dataclass(slots=True)
class PumpData:
    #: Settled run seconds per shift instance key (ISO timestamp of its start).
    totals: dict[str, float] = field(default_factory=dict)
    #: Start of the currently open run segment; None while stopped.
    segment_start: datetime | None = None
    #: Time through which the open segment has already been split into totals.
    settled_until: datetime | None = None
    #: True after recovery until the operator resumes: the restored segment is
    #: kept but frozen, so downtime is never extrapolated as running time.
    awaiting_resume: bool = False
    machine: StateMachine = field(default_factory=new_pump_machine)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def running(self) -> bool:
        return self.machine.state == PumpState.RUNNING
