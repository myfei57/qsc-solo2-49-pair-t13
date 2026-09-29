"""Pump start gate and start/resume paths."""

from __future__ import annotations

from ..domain.events import Event
from ..domain.kinds import EventKind
from .errors import PumpError, PumpErrorCode


class StartOps:
    """Mixed into :class:`~linecontrol.pump.pump.Pump`."""

    def start_gate(self) -> None:
        """Refuse to start unless the tank level has just been confirmed."""

        if not self.tank.level_ok():
            raise PumpError(
                PumpErrorCode.LEVEL_LOW,
                f"tank level {self.tank.level():.2f} below minimum",
            )

    def start(self) -> None:
        self.start_gate()
        self.data.machine.enter("running")
        self._begin_run("started")

    def resume(self) -> None:
        """Recovery path: re-evaluate the level before resuming circulation."""

        self.start_gate()
        # A recovered snapshot may already show the pump running; enter() is a
        # no-op for same-state transitions, so only flip when it was stopped.
        self.data.machine.enter("running")
        self._begin_run("resumed", continuing=True)

    def _begin_run(self, message: str, continuing: bool = False) -> None:
        # Anchor and persist the open segment before announcing the run so the
        # segment survives a crash even milliseconds after the start.
        now = self._clock()
        if continuing and self.data.awaiting_resume:
            self._resume_or_open_segment(now)
        else:
            self._open_segment(now)
        self.bus.publish(Event(kind=EventKind.PUMP, source="pump", message=message))
