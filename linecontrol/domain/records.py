"""Persisted and in-flight process records."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .events import utcnow


def _stamp(raw: Any) -> datetime:
    if isinstance(raw, datetime):
        stamp = raw
    elif isinstance(raw, str):
        stamp = datetime.fromisoformat(raw)
    else:
        stamp = utcnow()
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp


def _optional_stamp(raw: Any) -> datetime | None:
    if raw in (None, ""):
        return None
    return _stamp(raw)


def _number_like(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


@dataclass(frozen=True, slots=True)
class Confirm:
    """Outcome of a strip head alignment check."""

    ok: bool = False
    position: float = 0.0
    at: datetime = field(default_factory=utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "position": self.position, "at": self.at.isoformat()}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Confirm":
        return cls(
            ok=bool(raw.get("ok", False)),
            position=float(raw.get("position", 0.0)),
            at=_stamp(raw.get("at")),
        )


@dataclass(frozen=True, slots=True)
class WeldRecord:
    """A completed weld seam."""

    seam_id: str = ""
    position: float = 0.0
    tension: float = 0.0
    heat_input: float = 0.0
    at: datetime = field(default_factory=utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "seam_id": self.seam_id,
            "position": self.position,
            "tension": self.tension,
            "heat_input": self.heat_input,
            "at": self.at.isoformat(),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "WeldRecord":
        return cls(
            seam_id=str(raw.get("seam_id", "")),
            position=float(raw.get("position", 0.0)),
            tension=float(raw.get("tension", 0.0)),
            heat_input=float(raw.get("heat_input", 0.0)),
            at=_stamp(raw.get("at")),
        )


@dataclass(frozen=True, slots=True)
class TensionSnapshot:
    value: float = 0.0
    gain: float = 1.0
    saved: datetime = field(default_factory=utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {"value": self.value, "gain": self.gain, "saved": self.saved.isoformat()}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "TensionSnapshot":
        return cls(
            value=float(raw.get("value", 0.0)),
            gain=float(raw.get("gain", 1.0)),
            saved=_stamp(raw.get("saved")),
        )


@dataclass(frozen=True, slots=True)
class AcidSnapshot:
    level: float = 0.0
    offset: float = 0.0
    saved: datetime = field(default_factory=utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {"level": self.level, "offset": self.offset, "saved": self.saved.isoformat()}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "AcidSnapshot":
        return cls(
            level=float(raw.get("level", 0.0)),
            offset=float(raw.get("offset", 0.0)),
            saved=_stamp(raw.get("saved")),
        )


@dataclass(frozen=True, slots=True)
class PumpSnapshot:
    running: bool = False
    run_seconds: float = 0.0
    started_at: datetime | None = None
    shift_seconds: dict[str, float] = field(default_factory=dict)
    saved: datetime = field(default_factory=utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "run_seconds": self.run_seconds,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "shift_seconds": dict(self.shift_seconds),
            "saved": self.saved.isoformat(),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "PumpSnapshot":
        shifts = raw.get("shift_seconds", {})
        if not isinstance(shifts, dict):
            shifts = {}
        try:
            run_seconds = float(raw.get("run_seconds", 0.0))
        except (TypeError, ValueError):
            run_seconds = 0.0
        return cls(
            running=bool(raw.get("running", False)),
            run_seconds=max(run_seconds, 0.0),
            started_at=_optional_stamp(raw.get("started_at")),
            shift_seconds={
                str(key): max(float(value), 0.0)
                for key, value in shifts.items()
                if _number_like(value)
            },
            saved=_stamp(raw.get("saved")),
        )


@dataclass(frozen=True, slots=True)
class TrackSnapshot:
    position: float = 0.0
    seam_id: str = ""
    saved: datetime = field(default_factory=utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "position": self.position,
            "seam_id": self.seam_id,
            "saved": self.saved.isoformat(),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "TrackSnapshot":
        return cls(
            position=float(raw.get("position", 0.0)),
            seam_id=str(raw.get("seam_id", "")),
            saved=_stamp(raw.get("saved")),
        )


@dataclass(frozen=True, slots=True)
class RinseSnapshot:
    active: bool = False
    saved: datetime = field(default_factory=utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {"active": self.active, "saved": self.saved.isoformat()}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "RinseSnapshot":
        return cls(active=bool(raw.get("active", False)), saved=_stamp(raw.get("saved")))


@dataclass(frozen=True, slots=True)
class LatchSnapshot:
    interlock: bool = False
    alarm: bool = False
    saved: datetime = field(default_factory=utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "interlock": self.interlock,
            "alarm": self.alarm,
            "saved": self.saved.isoformat(),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "LatchSnapshot":
        return cls(
            interlock=bool(raw.get("interlock", False)),
            alarm=bool(raw.get("alarm", False)),
            saved=_stamp(raw.get("saved")),
        )
