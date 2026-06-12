"""ISO-TP frame parsing and reassembly."""

from __future__ import annotations

import pytest
from elm.obd import (
    NegativeResponseError,
    ObdDecodeError,
    assemble_isotp_messages,
    extract_payload,
    parse_frame_line,
)


def isotp_frames(can_id: int, payload: bytes) -> list[tuple[int, bytes]]:
    """Segment a payload exactly like an ECU would."""
    if len(payload) <= 7:
        return [(can_id, bytes([len(payload)]) + payload)]
    frames = [(can_id, bytes([0x10 | (len(payload) >> 8), len(payload) & 0xFF]) + payload[:6])]
    seq = 1
    for offset in range(6, len(payload), 7):
        frames.append((can_id, bytes([0x20 | (seq & 0x0F)]) + payload[offset : offset + 7]))
        seq += 1
    return frames


class TestParseFrameLine:
    def test_single_frame_line(self):
        assert parse_frame_line("7EC04628334D5") == (0x7EC, bytes.fromhex("04628334D5"))

    def test_line_with_spaces(self):
        assert parse_frame_line("7EC 04 62 83 34 D5") == (0x7EC, bytes.fromhex("04628334D5"))

    @pytest.mark.parametrize(
        "line",
        ["NO DATA", "SEARCHING...", "OK", "", "7EC", "7EC046", "ELM327 v1.5", "7EC046283ZZ"],
    )
    def test_non_frame_lines_rejected(self, line):
        assert parse_frame_line(line) is None


class TestAssembleIsotp:
    def test_single_frame(self):
        frames = [(0x7EC, bytes.fromhex("04628334D5"))]
        assert assemble_isotp_messages(frames, rx_id=0x7EC) == [bytes.fromhex("628334D5")]

    def test_multi_frame_round_trip(self):
        payload = bytes([0x49, 0x02, 0x01]) + b"1G1FY6S07L4100001"
        frames = isotp_frames(0x7E8, payload)
        assert len(frames) == 3  # FF + 2 CF
        assert assemble_isotp_messages(frames, rx_id=0x7E8) == [payload]

    def test_other_can_ids_filtered(self):
        payload = bytes.fromhex("628334D5")
        frames = [
            (0x7E9, bytes.fromhex("0499999999")),
            (0x7EC, bytes([len(payload)]) + payload),
        ]
        assert assemble_isotp_messages(frames, rx_id=0x7EC) == [payload]

    def test_sequence_break_drops_message(self):
        payload = bytes(range(20))
        first, _cf1, cf2 = isotp_frames(0x7EC, payload)
        assert assemble_isotp_messages([first, cf2], rx_id=0x7EC) == []

    def test_multiple_messages_in_one_read(self):
        pending = bytes([0x7F, 0x22, 0x78])
        answer = bytes.fromhex("628334D5")
        frames = [
            (0x7EC, bytes([len(pending)]) + pending),
            (0x7EC, bytes([len(answer)]) + answer),
        ]
        assert assemble_isotp_messages(frames, rx_id=0x7EC) == [pending, answer]


class TestExtractPayload:
    def test_positive_response(self):
        assert extract_payload([bytes.fromhex("628334D5")], 0x22, 0x8334, 2) == b"\xd5"

    def test_mode01_single_byte_pid(self):
        message = bytes.fromhex("41A6000C5A26")
        assert extract_payload([message], 0x01, 0xA6, 1) == bytes.fromhex("000C5A26")

    def test_pending_then_positive_prefers_positive(self):
        messages = [bytes([0x7F, 0x22, 0x78]), bytes.fromhex("628334D5")]
        assert extract_payload(messages, 0x22, 0x8334, 2) == b"\xd5"

    def test_negative_response_raises(self):
        with pytest.raises(NegativeResponseError) as excinfo:
            extract_payload([bytes([0x7F, 0x22, 0x31])], 0x22, 0x8334, 2)
        assert excinfo.value.nrc == 0x31

    def test_wrong_pid_echo_rejected(self):
        with pytest.raises(ObdDecodeError):
            extract_payload([bytes.fromhex("629999D5")], 0x22, 0x8334, 2)
