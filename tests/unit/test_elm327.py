"""ELM327 command layer against a scripted fake transport."""

from __future__ import annotations

import pytest
from elm.elm327 import Elm327, ElmProtocolError, NoDataError
from elm.obd import ObdSession


class FakeTransport:
    """Returns canned responses keyed by command; records writes.

    A response value may be a list to script different replies for repeated
    sends of the same command (consumed front-to-back, last one sticks).
    """

    def __init__(self, responses: dict[str, str | list[str]], default: str = "OK\r\r>") -> None:
        self.responses = responses
        self.default = default
        self.writes: list[str] = []
        self._pending: bytes | None = None

    def clear_buffer(self) -> None:
        self._pending = None

    async def write(self, data: bytes) -> None:
        cmd = data.decode().strip()
        self.writes.append(cmd)
        response = self.responses.get(cmd, self.default)
        if isinstance(response, list):
            response = response.pop(0) if len(response) > 1 else response[0]
        self._pending = response.encode()

    async def read_until(self, terminator: bytes = b">", timeout: float = 5.0) -> bytes:
        assert self._pending is not None, "read without write"
        data, self._pending = self._pending, None
        assert data.endswith(terminator)
        return data


@pytest.fixture
def elm_factory():
    def factory(responses: dict[str, str]) -> tuple[Elm327, FakeTransport]:
        transport = FakeTransport(responses)
        return Elm327(transport), transport  # type: ignore[arg-type]

    return factory


INIT_RESPONSES = {"ATZ": "ATZ\rELM327 v1.5\r\r>"}


async def test_initialize_runs_full_sequence(elm_factory):
    elm, transport = elm_factory(dict(INIT_RESPONSES))
    banner = await elm.initialize()
    assert banner == "ELM327 v1.5"
    assert transport.writes == ["ATZ", "ATE0", "ATL0", "ATS0", "ATH1", "ATSP6", "ATAT1"]


async def test_initialize_fails_on_unconfigured_adapter(elm_factory):
    elm, _ = elm_factory({**INIT_RESPONSES, "ATH1": "?\r\r>"})
    with pytest.raises(ElmProtocolError):
        await elm.initialize()


async def test_echo_lines_are_stripped(elm_factory):
    elm, _ = elm_factory({"228334": "228334\r7EC04628334D5\r\r>"})
    lines = await elm.command("228334")
    assert lines == ["7EC04628334D5"]


async def test_no_data_raises(elm_factory):
    elm, _ = elm_factory({"228334": "NO DATA\r\r>"})
    with pytest.raises(NoDataError):
        await elm.command("228334")


async def test_can_error_raises(elm_factory):
    elm, _ = elm_factory({"228334": "CAN ERROR\r\r>"})
    with pytest.raises(ElmProtocolError):
        await elm.command("228334")


async def test_voltage_parsing(elm_factory):
    elm, _ = elm_factory({"ATRV": "12.4V\r\r>"})
    assert await elm.voltage() == pytest.approx(12.4)


async def test_voltage_unparseable_returns_none(elm_factory):
    elm, _ = elm_factory({"ATRV": "?\r\r>"})
    assert await elm.voltage() is None


async def test_transcript_records_io(elm_factory):
    elm, _ = elm_factory({"ATRV": "12.4V\r\r>"})
    await elm.voltage()
    assert elm.transcript[-1][0] == "ATRV"
    assert "12.4V" in elm.transcript[-1][1]


class TestObdSession:
    async def test_query_sets_header_and_filter_once(self, elm_factory):
        elm, transport = elm_factory(
            {
                "228334": "7EC04628334D5\r\r>",
                "2243AF": "7EC05624 3AF8000\r\r>".replace(" ", ""),
            }
        )
        session = ObdSession(elm)
        payload = await session.query(0x22, 0x8334, tx_header=0x7E4)
        assert payload == b"\xd5"
        await session.query(0x22, 0x43AF, tx_header=0x7E4)
        # Header commands sent exactly once for the shared header.
        assert transport.writes.count("ATSH7E4") == 1
        assert transport.writes.count("ATCRA7EC") == 1

    async def test_pending_and_answer_in_single_read(self, elm_factory):
        elm, _ = elm_factory({"228334": "7EC037F2278\r7EC04628334D5\r\r>"})
        session = ObdSession(elm)
        assert await session.query(0x22, 0x8334, tx_header=0x7E4) == b"\xd5"

    async def test_query_retries_after_response_pending(self, elm_factory):
        elm, transport = elm_factory(
            {"228334": ["7EC037F2278\r\r>", "7EC04628334D5\r\r>"]}
        )
        session = ObdSession(elm)
        assert await session.query(0x22, 0x8334, tx_header=0x7E4) == b"\xd5"
        assert transport.writes.count("228334") == 2

    async def test_vin_multiframe(self, elm_factory):
        vin = b"1G1FY6S07L4100001"
        payload = bytes([0x49, 0x02, 0x01]) + vin
        first = f"7E810{len(payload):02X}" + payload[:6].hex().upper()
        cf1 = "7E821" + payload[6:13].hex().upper()
        cf2 = "7E822" + payload[13:20].hex().upper()
        elm, _ = elm_factory({"0902": f"{first}\r{cf1}\r{cf2}\r\r>"})
        session = ObdSession(elm)
        assert await session.read_vin() == vin.decode()

    async def test_vin_unreadable_returns_none(self, elm_factory):
        elm, _ = elm_factory({"0902": "NO DATA\r\r>"})
        session = ObdSession(elm)
        assert await session.read_vin() is None
