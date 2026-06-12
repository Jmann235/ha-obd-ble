"""Dataclasses describing a vehicle's OBD PID table."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

DecodeFunc = Callable[[bytes], float]
DeriveFunc = Callable[[Mapping[str, float | None]], float | None]


def u8(payload: bytes, index: int = 0) -> int:
    return payload[index]


def s8(payload: bytes, index: int = 0) -> int:
    return int.from_bytes(payload[index : index + 1], "big", signed=True)


def u16(payload: bytes, index: int = 0) -> int:
    if len(payload) < index + 2:
        raise ValueError(f"payload too short for u16 at {index}: {payload.hex()}")
    return int.from_bytes(payload[index : index + 2], "big")


def s16(payload: bytes, index: int = 0) -> int:
    if len(payload) < index + 2:
        raise ValueError(f"payload too short for s16 at {index}: {payload.hex()}")
    return int.from_bytes(payload[index : index + 2], "big", signed=True)


def u32(payload: bytes, index: int = 0) -> int:
    if len(payload) < index + 4:
        raise ValueError(f"payload too short for u32 at {index}: {payload.hex()}")
    return int.from_bytes(payload[index : index + 4], "big")


@dataclass(frozen=True)
class PidDefinition:
    """One pollable value.

    ``device_class`` / ``state_class`` are Home Assistant sensor vocabulary
    kept as plain strings here so this module stays HA-free; sensor.py maps
    them to the real enums.
    """

    key: str
    name: str
    mode: int
    pid: int
    tx_header: int
    decode: DecodeFunc
    unit: str | None
    pid_bytes: int = 2
    device_class: str | None = None
    state_class: str | None = "measurement"
    icon: str | None = None
    enabled_default: bool = True
    precision: int | None = 1
    verified: bool = True  # False = community-sourced, pending bench check

    def __post_init__(self) -> None:
        if not self.key.isidentifier():
            raise ValueError(f"key must be identifier-like: {self.key!r}")
        if not 0 < self.mode <= 0x3F:
            raise ValueError(f"{self.key}: mode out of range: {self.mode:#x}")
        if self.pid_bytes not in (1, 2):
            raise ValueError(f"{self.key}: pid_bytes must be 1 or 2")
        if not 0 <= self.pid < (1 << (8 * self.pid_bytes)):
            raise ValueError(f"{self.key}: pid {self.pid:#x} exceeds {self.pid_bytes} bytes")
        if not 0x700 <= self.tx_header <= 0x7FF:
            raise ValueError(f"{self.key}: tx_header {self.tx_header:#x} not an 11-bit OBD id")


@dataclass(frozen=True)
class DerivedDefinition:
    """A value computed from other decoded values (e.g. power = V * I)."""

    key: str
    name: str
    compute: DeriveFunc
    unit: str | None
    # PID keys this computation reads — they get polled even when their own
    # sensors are disabled, as long as this derived sensor is enabled.
    inputs: tuple[str, ...] = ()
    device_class: str | None = None
    state_class: str | None = "measurement"
    icon: str | None = None
    enabled_default: bool = True
    precision: int | None = 1


@dataclass(frozen=True)
class VehicleProfile:
    key: str
    name: str
    manufacturer: str
    model: str
    pids: tuple[PidDefinition, ...]
    derived: tuple[DerivedDefinition, ...] = ()
    # Returns True/False when determinable from the decoded values, else None.
    charging_detector: Callable[[Mapping[str, float | None]], bool | None] = field(
        default=lambda values: None
    )
    # PID keys the charging detector reads; always polled.
    charging_inputs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        keys = [p.key for p in self.pids] + [d.key for d in self.derived]
        duplicates = {k for k in keys if keys.count(k) > 1}
        if duplicates:
            raise ValueError(f"{self.key}: duplicate value keys {sorted(duplicates)}")

    @property
    def headers(self) -> tuple[int, ...]:
        """Distinct tx headers in table order (poll groups)."""
        seen: dict[int, None] = {}
        for pid in self.pids:
            seen.setdefault(pid.tx_header, None)
        return tuple(seen)

    def pids_for_header(self, tx_header: int) -> tuple[PidDefinition, ...]:
        return tuple(p for p in self.pids if p.tx_header == tx_header)
