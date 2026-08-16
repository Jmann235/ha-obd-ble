"""Kia EV6 profile: byte offsets proven end-to-end through ISO-TP reassembly.

The offsets in vehicles/kia_ev6.py are the whole risk of that profile, so
these tests do not call the decoders on hand-cut slices. They build a real
multi-frame ``62 01 xx`` response the way the BMS would, push it through
parse_frame_line -> assemble_isotp_messages -> extract_payload, and only
then decode. A frame-boundary or echo-length mistake fails here.
"""

from __future__ import annotations

import pytest
from elm.obd import assemble_isotp_messages, extract_payload, parse_frame_line
from vehicles.base import PidDefinition
from vehicles.registry import get_profile

BMS_RX = 0x7EC


def pid_by_key(profile, key: str) -> PidDefinition:
    return next(p for p in profile.pids if p.key == key)


def isotp_lines(can_id: int, message: bytes) -> list[str]:
    """Render a message as the ELM327 text lines an ATH1/ATS0 read returns."""
    prefix = f"{can_id:03X}"
    if len(message) <= 7:
        frame = bytes([len(message)]) + message
        return [prefix + frame.hex().upper()]

    lines = [
        prefix
        + (bytes([0x10 | (len(message) >> 8), len(message) & 0xFF]) + message[:6]).hex().upper()
    ]
    body, seq = message[6:], 1
    while body:
        chunk, body = body[:7], body[7:]
        frame = bytes([0x20 | (seq & 0x0F)]) + chunk
        frame = frame.ljust(8, b"\x00")  # the BMS pads the last frame
        lines.append(prefix + frame.hex().upper())
        seq += 1
    return lines


def roundtrip(mode: int, did: int, payload: bytes) -> bytes:
    """Payload -> CAN frames -> back to payload, as the integration sees it."""
    message = bytes([mode + 0x40]) + did.to_bytes(2, "big") + payload
    frames = [f for line in isotp_lines(BMS_RX, message) if (f := parse_frame_line(line))]
    messages = assemble_isotp_messages(frames, rx_id=BMS_RX)
    return extract_payload(messages, mode, did, 2)


def build_220101() -> bytes:
    """A plausible 220101 payload: 48 % SOC, charging at 19.3 A, 383.0 V."""
    p = bytearray(58)
    p[4] = 96  # BMS SOC 48.0 %
    p[10:12] = (-193).to_bytes(2, "big", signed=True)  # -19.3 A (charging)
    p[12:14] = (3830).to_bytes(2, "big")  # 383.0 V
    p[14] = 31  # max cell temp
    p[15] = 28  # min cell temp
    p[23] = 202  # max cell 4.04 V
    p[25] = 200  # min cell 4.00 V
    p[29] = 143  # 14.3 V aux
    p[30:34] = (123456).to_bytes(4, "big")  # 12345.6 Ah charged
    p[34:38] = (120000).to_bytes(4, "big")  # 12000.0 Ah discharged
    p[38:42] = (45678).to_bytes(4, "big")  # 4567.8 kWh charged
    p[42:46] = (44000).to_bytes(4, "big")  # 4400.0 kWh discharged
    return bytes(p)


def build_220105() -> bytes:
    """A plausible 220105 payload: 47.5 % dash SOC, 98.9 % SOH."""
    p = bytearray(45)
    p[25:27] = (989).to_bytes(2, "big")
    p[31] = 95
    return bytes(p)


@pytest.fixture(name="profile")
def profile_fixture():
    return get_profile("kia_ev6")


def test_profile_is_registered(profile):
    assert profile.manufacturer == "Kia"
    # One ECU, so one poll group and one probe.
    assert profile.headers == (0x7E4,)


def test_multi_frame_roundtrip_preserves_payload():
    payload = build_220101()
    assert roundtrip(0x22, 0x0101, payload) == payload


def test_220101_decoders(profile):
    payload = roundtrip(0x22, 0x0101, build_220101())
    d = {p.key: p.decode(payload) for p in profile.pids if p.pid == 0x0101}

    assert d["soc_bms"] == pytest.approx(48.0)
    assert d["hv_voltage"] == pytest.approx(383.0)
    assert d["hv_current"] == pytest.approx(-19.3)
    assert d["battery_temp_max"] == pytest.approx(31.0)
    assert d["battery_temp_min"] == pytest.approx(28.0)
    assert d["aux_voltage"] == pytest.approx(14.3)
    assert d["cell_voltage_max"] == pytest.approx(4.04)
    assert d["cell_voltage_min"] == pytest.approx(4.00)
    assert d["cumulative_charge_ah"] == pytest.approx(12345.6)
    assert d["cumulative_discharge_ah"] == pytest.approx(12000.0)
    assert d["cumulative_energy_charged"] == pytest.approx(4567.8)
    assert d["cumulative_energy_discharged"] == pytest.approx(4400.0)


def test_220105_decoders(profile):
    payload = roundtrip(0x22, 0x0105, build_220105())
    assert pid_by_key(profile, "soc").decode(payload) == pytest.approx(47.5)
    assert pid_by_key(profile, "soh").decode(payload) == pytest.approx(98.9)


def test_negative_temperatures_are_signed(profile):
    p = bytearray(build_220101())
    p[14:16] = (-7).to_bytes(1, "big", signed=True) + (-11).to_bytes(1, "big", signed=True)
    payload = roundtrip(0x22, 0x0101, bytes(p))
    assert pid_by_key(profile, "battery_temp_max").decode(payload) == pytest.approx(-7.0)
    assert pid_by_key(profile, "battery_temp_min").decode(payload) == pytest.approx(-11.0)


def test_hv_power_sign_is_negative_while_charging(profile):
    payload = roundtrip(0x22, 0x0101, build_220101())
    values = {p.key: p.decode(payload) for p in profile.pids if p.pid == 0x0101}
    (power,) = profile.derived
    # 383.0 V x -19.3 A = -7.39 kW, i.e. ~7.4 kW going into the pack.
    assert power.compute(values) == pytest.approx(-7.3919, abs=1e-3)


def test_charging_detector_uses_current_sign(profile):
    assert profile.charging_detector({"hv_current": -30.0}) is True
    assert profile.charging_detector({"hv_current": 5.0}) is False
    # The real observed low-rate charge. The deadband must not swallow it.
    assert profile.charging_detector({"hv_current": -1.4}) is True
    assert profile.charging_detector({"hv_current": -0.1}) is False
    assert profile.charging_detector({}) is None


def test_derived_power_tolerates_a_missing_input(profile):
    (power,) = profile.derived
    assert power.compute({"hv_voltage": 383.0}) is None
    assert power.compute({}) is None


# --- Real capture, 2022 EV6 Long Range, 2026-08-16 18:05 local --------------
# Lifted verbatim from the integration's diagnostics transcript while the car
# was on the wallbox at ~1.3 kW. This is the regression anchor: synthetic
# frames prove the arithmetic, but only real ones prove the byte map.
CAPTURE_220101 = [
    "7EC103E620101EFFBE7",
    "7EC21EF6B2040017200",
    "7EC22FFF21C16201E20",
    "7EC23201F1E20004BBB",
    "7EC240DBB3D00008D00",
    "7EC2500D09F0000D37D",
    "7EC2600009CDF00009B",
    "7EC274400895F240002",
    "7EC28CD000000000BB8",
]
CAPTURE_220105 = [
    "7EC102E620105FFFB74",
    "7EC210F012C01012C1F",
    "7EC22201F1F1F1F1E6C",
    "7EC23346C3400006422",
    "7EC240003E805480200",
    "7EC256A000000000000",
    "7EC26001F1E1F20AAAA",
]


def decode_capture(lines: list[str], did: int) -> bytes:
    frames = [f for line in lines if (f := parse_frame_line(line))]
    return extract_payload(assemble_isotp_messages(frames, rx_id=BMS_RX), 0x22, did, 2)


def test_real_capture_220101(profile):
    payload = decode_capture(CAPTURE_220101, 0x0101)
    d = {p.key: p.decode(payload) for p in profile.pids if p.pid == 0x0101}

    assert d["hv_voltage"] == pytest.approx(719.0)
    assert d["hv_current"] == pytest.approx(-1.4)  # charging, slowly
    assert d["soc_bms"] == pytest.approx(53.5)
    assert d["battery_temp_max"] == pytest.approx(32.0)
    assert d["battery_temp_min"] == pytest.approx(30.0)
    assert d["aux_voltage"] == pytest.approx(14.1)
    assert d["cumulative_energy_charged"] == pytest.approx(4015.9)
    assert d["cumulative_energy_discharged"] == pytest.approx(3974.8)


def test_real_capture_220105(profile):
    payload = decode_capture(CAPTURE_220105, 0x0105)
    assert pid_by_key(profile, "soc").decode(payload) == pytest.approx(53.0)
    assert pid_by_key(profile, "soh").decode(payload) == pytest.approx(100.0)


def test_cell_voltages_reconstruct_the_pack(profile):
    """192 cells x the measured cell voltage must land on the pack voltage.

    This is the check that turns a plausible byte map into a proven one: the
    cell-voltage and pack-voltage offsets are in different parts of the frame,
    so agreeing to 0.2% by accident is not credible.
    """
    payload = decode_capture(CAPTURE_220101, 0x0101)
    cell = pid_by_key(profile, "cell_voltage_max").decode(payload)
    pack = pid_by_key(profile, "hv_voltage").decode(payload)
    assert cell == pytest.approx(3.74)
    assert 192 * cell == pytest.approx(pack, rel=0.005)


def test_counters_imply_a_plausible_800v_pack(profile):
    """kWh / Ah must land on a sane average pack voltage, charge above discharge."""
    payload = decode_capture(CAPTURE_220101, 0x0101)
    d = {p.key: p.decode(payload) for p in profile.pids if p.pid == 0x0101}
    v_charge = d["cumulative_energy_charged"] / d["cumulative_charge_ah"] * 1000
    v_discharge = d["cumulative_energy_discharged"] / d["cumulative_discharge_ah"] * 1000
    assert 700 < v_discharge < v_charge < 800
