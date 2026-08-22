"""Kia Ceed PHEV profile: offsets proven end-to-end through ISO-TP reassembly.

Same standard as the EV6 tests — nothing is decoded from a hand-cut slice.
Payloads are rendered into real CAN frames, pushed through
parse_frame_line -> assemble_isotp_messages -> extract_payload, and only then
decoded, so a frame-boundary or echo-length mistake fails here.

The echo length is the point worth stressing on this profile. Every other
profile in the repo uses service 0x22 with a two-byte identifier, so the echo
is three bytes; this one uses service 0x21 with a one-byte local id, so the
echo is two. That difference is invisible in the decoders and fatal if
extract_payload gets it wrong, which is why test_echo_length_is_two_bytes
exists.

The physical cross-checks that would turn these offsets from
well-corroborated into proven are written at the bottom and skipped, waiting
on a bench capture from the car. Filling in CAPTURE_2101 is the only edit
needed to run them.
"""

from __future__ import annotations

import pytest
from elm.obd import assemble_isotp_messages, extract_payload, parse_frame_line
from vehicles.base import PidDefinition
from vehicles.kia_ceed_phev import (
    CELL_COUNT,
    FLAG_NORMAL_CHARGE_PORT,
    FLAG_RAPID_CHARGE_PORT,
)
from vehicles.registry import get_profile

BMS_RX = 0x7EC
MODE = 0x21


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


def roundtrip(did: int, payload: bytes) -> bytes:
    """Payload -> CAN frames -> back to payload, as the integration sees it.

    Note the two-byte echo: 61 <did>, not 62 <hi> <lo>.
    """
    message = bytes([MODE + 0x40, did]) + payload
    frames = [f for line in isotp_lines(BMS_RX, message) if (f := parse_frame_line(line))]
    messages = assemble_isotp_messages(frames, rx_id=BMS_RX)
    return extract_payload(messages, MODE, did, 1)


def build_2101(*, flags: int = 0x00) -> bytes:
    """A plausible DID 0x01 payload for a 64-cell, ~240 V PHEV pack.

    58 % SOC, 13.8 A flowing into the pack at 241.6 V — i.e. a 3.3 kW onboard
    charger doing its job.
    """
    p = bytearray(59)
    p[4] = 116  # BMS SOC 58.0 %
    p[5:7] = (4500).to_bytes(2, "big")  # max charge power 45.00 kW
    p[7:9] = (6000).to_bytes(2, "big")  # max discharge power 60.00 kW
    p[9] = flags
    p[10:12] = (-138).to_bytes(2, "big", signed=True)  # -13.8 A (into pack)
    p[12:14] = (2416).to_bytes(2, "big")  # 241.6 V
    p[14] = 24  # max cell temp
    p[15] = 21  # min cell temp
    p[22] = 22  # inlet temp — must sit near the module temps
    p[23] = 189  # max cell 3.78 V
    p[25] = 187  # min cell 3.74 V
    p[29] = 144  # 14.4 V aux
    p[30:34] = (54321).to_bytes(4, "big")  # 5432.1 Ah charged
    p[34:38] = (53000).to_bytes(4, "big")  # 5300.0 Ah discharged
    p[38:42] = (13100).to_bytes(4, "big")  # 1310.0 kWh charged
    p[42:46] = (12800).to_bytes(4, "big")  # 1280.0 kWh discharged
    p[46:50] = (3600 * 421).to_bytes(4, "big")  # 421 h operating time
    p[53:55] = (1450).to_bytes(2, "big", signed=True)  # 1450 rpm
    return bytes(p)


def build_2105() -> bytes:
    """A plausible DID 0x05 payload: 96.4 % SOH, 57.5 % dash SOC."""
    p = bytearray(45)
    p[20] = 2  # 0.04 V cell deviation
    p[35:37] = (964).to_bytes(2, "big")
    p[38] = 115
    return bytes(p)


@pytest.fixture(name="profile")
def profile_fixture():
    return get_profile("kia_ceed_phev")


def test_profile_is_registered(profile):
    assert profile.manufacturer == "Kia"
    # One ECU, so one poll group and one probe.
    assert profile.headers == (0x7E4,)


def test_profile_uses_service_21_with_one_byte_ids(profile):
    """The whole platform difference from E-GMP, asserted once."""
    assert {p.mode for p in profile.pids} == {0x21}
    assert {p.pid_bytes for p in profile.pids} == {1}
    assert {p.pid for p in profile.pids} == {0x01, 0x05}


def test_echo_length_is_two_bytes():
    """61 01 must be stripped, not 61 01 xx.

    If extract_payload assumed a two-byte identifier the whole table would be
    off by one and every value would still look superficially plausible.
    """
    payload = bytes(range(1, 40))
    message = bytes([0x61, 0x01]) + payload
    frames = [f for line in isotp_lines(BMS_RX, message) if (f := parse_frame_line(line))]
    messages = assemble_isotp_messages(frames, rx_id=BMS_RX)
    assert extract_payload(messages, 0x21, 0x01, 1) == payload


def test_multi_frame_roundtrip_preserves_payload():
    payload = build_2101()
    assert roundtrip(0x01, payload) == payload


def test_2101_decoders(profile):
    payload = roundtrip(0x01, build_2101())
    d = {p.key: p.decode(payload) for p in profile.pids if p.pid == 0x01}

    assert d["soc_bms"] == pytest.approx(58.0)
    assert d["hv_voltage"] == pytest.approx(241.6)
    assert d["hv_current"] == pytest.approx(-13.8)
    assert d["battery_temp_max"] == pytest.approx(24.0)
    assert d["battery_temp_min"] == pytest.approx(21.0)
    assert d["battery_inlet_temp"] == pytest.approx(22.0)
    assert d["cell_voltage_max"] == pytest.approx(3.78)
    assert d["cell_voltage_min"] == pytest.approx(3.74)
    assert d["aux_voltage"] == pytest.approx(14.4)
    assert d["max_charge_power"] == pytest.approx(45.0)
    assert d["max_discharge_power"] == pytest.approx(60.0)
    assert d["cumulative_charge_ah"] == pytest.approx(5432.1)
    assert d["cumulative_discharge_ah"] == pytest.approx(5300.0)
    assert d["cumulative_energy_charged"] == pytest.approx(1310.0)
    assert d["cumulative_energy_discharged"] == pytest.approx(1280.0)
    assert d["operating_time"] == pytest.approx(421.0)
    assert d["motor_speed"] == pytest.approx(1450.0)


def test_2105_decoders(profile):
    payload = roundtrip(0x05, build_2105())
    assert pid_by_key(profile, "soh").decode(payload) == pytest.approx(96.4)
    assert pid_by_key(profile, "soc_display").decode(payload) == pytest.approx(57.5)
    assert pid_by_key(profile, "cell_voltage_deviation").decode(payload) == pytest.approx(0.04)


def test_negative_temperatures_are_signed(profile):
    p = bytearray(build_2101())
    p[14:16] = (-4).to_bytes(1, "big", signed=True) + (-9).to_bytes(1, "big", signed=True)
    p[22] = 0xF9  # -7
    payload = roundtrip(0x01, bytes(p))
    assert pid_by_key(profile, "battery_temp_max").decode(payload) == pytest.approx(-4.0)
    assert pid_by_key(profile, "battery_temp_min").decode(payload) == pytest.approx(-9.0)
    assert pid_by_key(profile, "battery_inlet_temp").decode(payload) == pytest.approx(-7.0)


def test_regen_is_not_reported_as_charging(profile):
    """The PHEV-specific trap, and the reason this detector differs from E-GMP.

    Current into the pack with no plug connected is regen or the engine
    driving the HSG. The EV6 rule (current < -0.2) would call this charging
    and be wrong for a large fraction of every drive.
    """
    payload = roundtrip(0x01, build_2101(flags=0x00))
    values = {p.key: p.decode(payload) for p in profile.pids if p.pid == 0x01}
    assert values["hv_current"] < 0  # current really is flowing in
    assert profile.charging_detector(values) is False


def test_plugged_in_and_flowing_is_charging(profile):
    payload = roundtrip(0x01, build_2101(flags=FLAG_NORMAL_CHARGE_PORT))
    values = {p.key: p.decode(payload) for p in profile.pids if p.pid == 0x01}
    assert profile.charging_detector(values) is True


def test_plugged_in_but_finished_is_not_charging(profile):
    """Port occupied, nothing flowing — a completed charge, not an active one."""
    p = bytearray(build_2101(flags=FLAG_NORMAL_CHARGE_PORT))
    p[10:12] = (0).to_bytes(2, "big", signed=True)
    values = {
        pid.key: pid.decode(roundtrip(0x01, bytes(p))) for pid in profile.pids if pid.pid == 0x01
    }
    assert profile.charging_detector(values) is False


def test_charging_detector_declines_to_guess_without_flags(profile):
    assert profile.charging_detector({"hv_current": -13.8}) is None
    assert profile.charging_detector({}) is None


def test_hv_power_sign_is_negative_while_charging(profile):
    payload = roundtrip(0x01, build_2101(flags=FLAG_NORMAL_CHARGE_PORT))
    values = {p.key: p.decode(payload) for p in profile.pids if p.pid == 0x01}
    (power,) = profile.derived
    # 241.6 V x -13.8 A = -3.33 kW, i.e. ~3.3 kW into the pack — which is
    # exactly the Ceed PHEV's onboard AC charger rating.
    assert power.compute(values) == pytest.approx(-3.3341, abs=1e-3)


def test_derived_power_tolerates_a_missing_input(profile):
    (power,) = profile.derived
    assert power.compute({"hv_voltage": 241.6}) is None
    assert power.compute({}) is None


def test_every_row_is_marked_unverified(profile):
    """Nothing here has met a Ceed yet. If a row gets promoted, it should be
    a deliberate edit with a capture behind it, not a default."""
    assert [p.key for p in profile.pids if p.verified] == []


def test_dash_soc_is_not_the_default_battery_sensor(profile):
    """DID 0x05's layout is contested three ways, so the headline battery
    number must come from the triple-corroborated DID 0x01 row."""
    assert pid_by_key(profile, "soc_bms").enabled_default is True
    assert pid_by_key(profile, "soc_display").enabled_default is False
    assert pid_by_key(profile, "soh").enabled_default is False


# --- Awaiting a real capture from the car ------------------------------------
# Paste the 2101 frame lines from `bench.py sweep --state charging` (or the
# integration's Download diagnostics) into CAPTURE_2101 and drop the skip
# marks. These are the checks that make the byte map proven rather than
# merely well-corroborated — the same two the EV6 profile rests on, scaled to
# a 64-cell pack.
CAPTURE_2101: list[str] = []


@pytest.mark.skipif(not CAPTURE_2101, reason="awaiting bench capture from the Ceed")
def test_real_capture_cell_voltages_reconstruct_the_pack(profile):
    """64 cells x measured cell voltage must land on the pack voltage.

    Cell voltage and pack voltage live in different parts of the frame, so
    agreeing to within a percent by accident is not credible.
    """
    frames = [f for line in CAPTURE_2101 if (f := parse_frame_line(line))]
    payload = extract_payload(assemble_isotp_messages(frames, rx_id=BMS_RX), MODE, 0x01, 1)
    cell = pid_by_key(profile, "cell_voltage_max").decode(payload)
    pack = pid_by_key(profile, "hv_voltage").decode(payload)
    assert 3.0 < cell < 4.3
    assert CELL_COUNT * cell == pytest.approx(pack, rel=0.01)


@pytest.mark.skipif(not CAPTURE_2101, reason="awaiting bench capture from the Ceed")
def test_real_capture_counters_imply_a_plausible_240v_pack(profile):
    """kWh / Ah must land on a sane average pack voltage, charge above discharge."""
    frames = [f for line in CAPTURE_2101 if (f := parse_frame_line(line))]
    payload = extract_payload(assemble_isotp_messages(frames, rx_id=BMS_RX), MODE, 0x01, 1)
    d = {p.key: p.decode(payload) for p in profile.pids if p.pid == 0x01}
    v_charge = d["cumulative_energy_charged"] / d["cumulative_charge_ah"] * 1000
    v_discharge = d["cumulative_energy_discharged"] / d["cumulative_discharge_ah"] * 1000
    assert 200 < v_discharge < v_charge < 290


@pytest.mark.skipif(not CAPTURE_2101, reason="awaiting bench capture from the Ceed")
def test_real_capture_inlet_temp_is_near_the_cell_temps(profile):
    """Byte 22 is the offset E-GMP had to drop. On this platform it should
    read within a few degrees of the pack, not tens."""
    frames = [f for line in CAPTURE_2101 if (f := parse_frame_line(line))]
    payload = extract_payload(assemble_isotp_messages(frames, rx_id=BMS_RX), MODE, 0x01, 1)
    inlet = pid_by_key(profile, "battery_inlet_temp").decode(payload)
    t_min = pid_by_key(profile, "battery_temp_min").decode(payload)
    t_max = pid_by_key(profile, "battery_temp_max").decode(payload)
    assert t_min - 10 <= inlet <= t_max + 10


@pytest.mark.skipif(not CAPTURE_2101, reason="awaiting bench capture from the Ceed")
def test_real_capture_has_no_dc_port(profile):
    """The Ceed PHEV is AC-only. Bit 6 set would mean byte 9 is not the flag
    byte on this platform, the same way it was not on E-GMP."""
    frames = [f for line in CAPTURE_2101 if (f := parse_frame_line(line))]
    payload = extract_payload(assemble_isotp_messages(frames, rx_id=BMS_RX), MODE, 0x01, 1)
    flags = int(pid_by_key(profile, "bms_flags").decode(payload))
    assert not flags & FLAG_RAPID_CHARGE_PORT
