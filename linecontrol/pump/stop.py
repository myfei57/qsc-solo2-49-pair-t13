"""Pump stop gate and stop handling."""

from __future__ import annotations

from ..domain.events import Event
from ..domain.kinds import EventKind
from .errors import PumpError, PumpErrorCode


class StopOps:
    """Mixed into :class:`~linecontrol.pump.pump.Pump`."""

    def stop_gate(self) -> None:
        """The pump may only stop once the strip has come to rest."""

        if not self.strip_stopped():
            raise PumpError(PumpErrorCode.STRIP_RUN, "strip still running")

    def stop(self) -> None:
        self.stop_gate()
        self.data.machine.enter("stopped")
        self._close_segment(self._clock())
        self.bus.publish(Event(kind=EventKind.PUMP, source="pump", message="stopped"))
