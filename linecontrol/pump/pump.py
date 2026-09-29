"""Circulation pump controller."""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import datetime

from ..bus import EventBus
from ..pickle.tank import Tank
from ..store.filestore import FileStore
from .replenish import ReplenishOps
from .runhours import ShiftRunOps
from .schedule import ShiftSchedule
from .start import StartOps
from .state import PumpData
from .stop import StopOps

#: Interval between incremental run-time settlements while running.
DEFAULT_TICK_SECONDS = 1.0


class Pump(StartOps, StopOps, ReplenishOps, ShiftRunOps):
    """Gates circulation on a freshly evaluated tank level."""

    def __init__(
        self,
        tank: Tank,
        store: FileStore,
        bus: EventBus,
        strip_stopped: Callable[[], bool],
        schedule: ShiftSchedule | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._tank = tank
        self._store = store
        self._bus = bus
        self._strip_stopped = strip_stopped
        self._state = PumpData()
        self.__init_ledger__(schedule, clock or _default_clock)
        self._tick_seconds = DEFAULT_TICK_SECONDS
        self._ticker: threading.Thread | None = None
        self._ticker_stop = threading.Event()

    @property
    def tank(self) -> Tank:
        return self._tank

    @property
    def store(self) -> FileStore:
        return self._store

    @property
    def bus(self) -> EventBus:
        return self._bus

    @property
    def data(self) -> PumpData:
        return self._state

    def strip_stopped(self) -> bool:
        return self._strip_stopped()

    def start_ticker(self) -> None:
        """Begin background settlement so cross-shift runs carry over live."""

        if self._ticker and self._ticker.is_alive():
            return
        self._ticker_stop.clear()
        thread = threading.Thread(
            target=self._tick_loop, name="pump-runhours", daemon=True
        )
        self._ticker = thread
        thread.start()

    def stop_ticker(self) -> None:
        """Stop the settlement loop (without settling a still-open segment)."""

        self._ticker_stop.set()
        thread = self._ticker
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self._ticker = None

    def flush(self) -> None:
        """Settle through now: call on graceful shutdown to lose no seconds."""

        self.settle()

    def _tick_loop(self) -> None:
        while not self._ticker_stop.wait(self._tick_seconds):
            try:
                self.settle()
            except Exception:  # pragma: no cover - a bad tick must not kill the loop
                pass

    def snapshot(self) -> dict[str, object]:
        persisted, _ = self.persisted()
        return {
            "running": self.running(),
            "runSec": self.run_seconds(),
            "persisted": persisted.running,
            "startedAt": self.started_at().isoformat() if self.started_at() else None,
            "shift": self.shift_report(limit=1),
            "transitions": list(self.data.machine.history()),
        }


def _default_clock() -> datetime:
    from ..domain.records import utcnow

    return utcnow()
