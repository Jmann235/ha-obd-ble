"""ELM327 AT-command layer on top of the BLE serial transport.

Init strategy: echo/linefeeds/spaces off for compact parsing, headers ON so
every CAN frame line arrives as ``<11-bit id><data hex>`` (PCI bytes
included), protocol fixed to ISO 15765-4 CAN 11bit/500k (every OBD-II car
since 2008, including the Bolt). The ELM still auto-handles ISO-TP flow
control, so multi-frame responses simply appear as multiple lines.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections import deque
from collections.abc import Iterable

from .transport import BleSerialTransport

_LOGGER = logging.getLogger(__name__)

PROMPT = b">"
RESET_TIMEOUT = 10.0
COMMAND_TIMEOUT = 5.0

# Messages the ELM emits instead of (or alongside) data.
FATAL_RESPONSES: tuple[str, ...] = (
    "UNABLE TO CONNECT",
    "CAN ERROR",
    "BUS INIT",
    "BUS BUSY",
    "FB ERROR",
    "DATA ERROR",
    "BUFFER FULL",
    "STOPPED",
)

_VOLTAGE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*V", re.IGNORECASE)

INIT_COMMANDS: tuple[str, ...] = (
    "ATE0",  # echo off
    "ATL0",  # linefeeds off
    "ATS0",  # spaces off
    "ATH1",  # headers on (frame lines include CAN id + PCI)
    "ATSP6",  # ISO 15765-4 CAN 11bit/500k, fixed (no protocol search)
    "ATAT1",  # adaptive timing
)


class ElmError(Exception):
    """Base for ELM327-level failures."""


class ElmProtocolError(ElmError):
    """The adapter reported a bus/protocol error; retry next cycle."""


class ElmCommandError(ElmError):
    """The adapter did not understand a command ('?')."""


class NoDataError(ElmError):
    """The car's module did not answer — typically the car is asleep."""


class Elm327:
    """Command/response driver for an ELM327-compatible adapter."""

    def __init__(self, transport: BleSerialTransport) -> None:
        self._transport = transport
        self._lock = asyncio.Lock()
        # (command, raw response) pairs for diagnostics dumps.
        self.transcript: deque[tuple[str, str]] = deque(maxlen=128)

    async def command(
        self, cmd: str, timeout: float = COMMAND_TIMEOUT, check_errors: bool = True
    ) -> list[str]:
        """Send one command, return cleaned response lines (prompt stripped)."""
        async with self._lock:
            self._transport.clear_buffer()
            await self._transport.write(cmd.encode("ascii") + b"\r")
            raw = await self._transport.read_until(PROMPT, timeout=timeout)

        text = raw.decode("ascii", errors="replace")
        self.transcript.append((cmd, text))

        lines = [line.strip() for line in text.replace("\r", "\n").split("\n")]
        lines = [line for line in lines if line and line != ">"]

        # Until ATE0 takes effect the adapter echoes the command back.
        normalized_cmd = cmd.replace(" ", "").upper()
        while lines and lines[0].replace(" ", "").upper() == normalized_cmd:
            lines.pop(0)

        if check_errors:
            self._raise_for_errors(cmd, lines)
        return lines

    @staticmethod
    def _raise_for_errors(cmd: str, lines: Iterable[str]) -> None:
        joined = " | ".join(lines)
        upper = joined.upper()
        for fatal in FATAL_RESPONSES:
            if fatal in upper:
                raise ElmProtocolError(f"{cmd!r} -> {joined!r}")
        if "NO DATA" in upper:
            raise NoDataError(f"{cmd!r} -> no response from vehicle")
        if joined.strip() == "?":
            raise ElmCommandError(f"adapter did not understand {cmd!r}")

    async def initialize(self) -> str:
        """Reset and configure the adapter; returns the identification banner."""
        # ATZ resets every setting (and re-enables echo), so it runs first
        # with a generous timeout while the chip reboots.
        banner_lines = await self.command("ATZ", timeout=RESET_TIMEOUT, check_errors=False)
        banner = next((line for line in banner_lines if "ELM" in line or "STN" in line), "")

        for cmd in INIT_COMMANDS:
            lines = await self.command(cmd, check_errors=False)
            if not any("OK" in line.upper() for line in lines):
                # Settings commands answer OK; anything else is a config failure
                # worth surfacing now rather than as garbled frames later.
                raise ElmProtocolError(f"init {cmd!r} -> {' | '.join(lines)!r}")

        _LOGGER.debug("ELM initialized: %s", banner or "(no banner)")
        return banner

    async def voltage(self) -> float | None:
        """Adapter-measured 12 V rail via ATRV; works whenever powered."""
        lines = await self.command("ATRV", check_errors=False)
        for line in lines:
            match = _VOLTAGE_RE.search(line)
            if match:
                return float(match.group(1))
        return None
