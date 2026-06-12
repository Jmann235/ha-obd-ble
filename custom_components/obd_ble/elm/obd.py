"""OBD-II / GM-extended queries over an Elm327, with ISO-TP reassembly.

Frame lines arrive as ``<3 hex CAN id><hex data>`` (ATH1 + ATS0). A request
to a module at ``tx_header`` (e.g. 0x7E4, the Bolt BECM) is answered from
``tx_header + 8`` (0x7EC). Multi-frame responses are ISO 15765-2 segmented;
the ELM auto-sends flow control, we reassemble the printed frames.
"""

from __future__ import annotations

import asyncio
import logging
import re

from .elm327 import COMMAND_TIMEOUT, Elm327, ElmCommandError, ElmError, NoDataError

_LOGGER = logging.getLogger(__name__)

_HEX_LINE_RE = re.compile(r"^[0-9A-Fa-f]+$")

RESPONSE_PENDING_NRC = 0x78

NRC_NAMES: dict[int, str] = {
    0x11: "service not supported",
    0x12: "sub-function not supported",
    0x22: "conditions not correct",
    0x31: "request out of range",
    0x33: "security access denied",
    0x78: "response pending",
}


class ObdDecodeError(ElmError):
    """Response arrived but could not be parsed into the expected payload."""


class NegativeResponseError(ElmError):
    """Module answered 7F <service> <nrc>."""

    def __init__(self, service: int, nrc: int) -> None:
        self.service = service
        self.nrc = nrc
        name = NRC_NAMES.get(nrc, "unknown")
        super().__init__(f"negative response to service {service:02X}: NRC {nrc:02X} ({name})")


def parse_frame_line(line: str) -> tuple[int, bytes] | None:
    """Parse one ``7EC066241A3...`` line into (can_id, data bytes).

    Returns None for non-frame lines (status text, corrupt hex) so callers
    can skip them.
    """
    compact = line.replace(" ", "")
    # 3 hex chars of 11-bit id + at least one data byte, even hex length after id.
    if len(compact) < 5 or not _HEX_LINE_RE.match(compact) or (len(compact) - 3) % 2 != 0:
        return None
    return int(compact[:3], 16), bytes.fromhex(compact[3:])


def assemble_isotp_messages(frames: list[tuple[int, bytes]], rx_id: int | None) -> list[bytes]:
    """Reassemble complete ISO-TP messages from raw frames.

    A single ELM read can contain several complete messages (e.g. a
    7F..78 'response pending' single frame followed by the real multi-frame
    answer), so all of them are returned in arrival order. Incomplete or
    out-of-sequence transfers are dropped.
    """
    messages: list[bytes] = []
    expected_len = 0
    assembly: bytearray | None = None
    next_seq = 0

    for can_id, data in frames:
        if rx_id is not None and can_id != rx_id:
            continue
        if not data:
            continue

        frame_type = data[0] >> 4
        if frame_type == 0:  # single frame
            length = data[0] & 0x0F
            if 0 < length <= len(data) - 1:
                messages.append(bytes(data[1 : 1 + length]))
            assembly = None
        elif frame_type == 1:  # first frame of a segmented message
            expected_len = ((data[0] & 0x0F) << 8) | data[1]
            assembly = bytearray(data[2:])
            next_seq = 1
        elif frame_type == 2 and assembly is not None:  # consecutive frame
            if (data[0] & 0x0F) != (next_seq & 0x0F):
                _LOGGER.debug("ISO-TP sequence break from %03X, dropping message", can_id)
                assembly = None
                continue
            assembly.extend(data[1:])
            next_seq += 1
            if len(assembly) >= expected_len:
                messages.append(bytes(assembly[:expected_len]))
                assembly = None
        # frame_type == 3 is flow control — not addressed to us, ignore.

    return messages


def extract_payload(messages: list[bytes], mode: int, pid: int, pid_bytes: int) -> bytes:
    """Pull the data bytes out of the positive response message.

    Raises NegativeResponseError if the module only sent a refusal, with
    'response pending' (NRC 0x78) surfaced distinctly so the caller can
    retry the request.
    """
    positive_service = (mode + 0x40) & 0xFF
    pid_echo = pid.to_bytes(pid_bytes, "big")
    negative: NegativeResponseError | None = None

    for message in messages:
        if len(message) >= 3 and message[0] == 0x7F:
            candidate = NegativeResponseError(message[1], message[2])
            if negative is None or negative.nrc == RESPONSE_PENDING_NRC:
                negative = candidate
            continue
        if (
            len(message) >= 1 + pid_bytes
            and message[0] == positive_service
            and message[1 : 1 + pid_bytes] == pid_echo
        ):
            return message[1 + pid_bytes :]

    if negative is not None:
        raise negative
    raise ObdDecodeError(
        f"no positive response for {mode:02X}/{pid:0{pid_bytes * 2}X} in {messages!r}"
    )


class ObdSession:
    """Stateful query helper that tracks the active ECU header."""

    def __init__(self, elm: Elm327) -> None:
        self._elm = elm
        self._current_header: int | None = None
        self._cra_supported = True

    @property
    def elm(self) -> Elm327:
        return self._elm

    async def _apply_header(self, tx_header: int) -> None:
        if tx_header == self._current_header:
            return
        await self._elm.command(f"ATSH{tx_header:03X}", check_errors=False)
        if self._cra_supported:
            try:
                # Filter received frames to this module's response id; some
                # clones lack ATCRA, in which case reassembly's rx_id filter
                # still protects us.
                await self._elm.command(f"ATCRA{tx_header + 8:03X}")
            except ElmCommandError:
                _LOGGER.debug("Adapter does not support ATCRA; relying on rx_id filter")
                self._cra_supported = False
        self._current_header = tx_header

    def reset_header_cache(self) -> None:
        """Call after re-initializing the adapter."""
        self._current_header = None

    async def query(
        self,
        mode: int,
        pid: int,
        *,
        pid_bytes: int = 2,
        tx_header: int = 0x7E4,
        timeout: float = COMMAND_TIMEOUT,
        _retried: bool = False,
    ) -> bytes:
        """Request mode/pid from the module at tx_header; return payload bytes."""
        await self._apply_header(tx_header)
        request = f"{mode:02X}{pid:0{pid_bytes * 2}X}"
        lines = await self._elm.command(request, timeout=timeout)

        frames = [frame for line in lines if (frame := parse_frame_line(line)) is not None]
        if not frames:
            raise ObdDecodeError(f"no CAN frames in response to {request!r}: {lines!r}")

        messages = assemble_isotp_messages(frames, rx_id=tx_header + 8)
        try:
            return extract_payload(messages, mode, pid, pid_bytes)
        except NegativeResponseError as err:
            if err.nrc == RESPONSE_PENDING_NRC and not _retried:
                await asyncio.sleep(0.3)
                return await self.query(
                    mode,
                    pid,
                    pid_bytes=pid_bytes,
                    tx_header=tx_header,
                    timeout=timeout,
                    _retried=True,
                )
            raise

    async def read_vin(self) -> str | None:
        """Mode 09 PID 02 from the ECM; returns None if unreadable."""
        try:
            payload = await self.query(0x09, 0x02, pid_bytes=1, tx_header=0x7E0)
        except (ElmError, TimeoutError) as err:
            _LOGGER.debug("VIN read failed: %s", err)
            return None
        # SAE J1979: a count byte (usually 0x01) precedes the 17 ASCII chars.
        if payload and payload[0] in (0x01, 0x00):
            payload = payload[1:]
        vin = payload.decode("ascii", errors="ignore").strip("\x00 ").strip()
        return vin if len(vin) == 17 else None


__all__ = [
    "NegativeResponseError",
    "NoDataError",
    "ObdDecodeError",
    "ObdSession",
    "assemble_isotp_messages",
    "extract_payload",
    "parse_frame_line",
]
