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

The physical cross-checks at the bottom run against a real capture taken from
the car on 2026-08-22 and are what turn the DID 0x01 offsets from
well-corroborated into proven.

The mode 01 rows at the end are a different kind of test. Their decodes are
J1979, not guesswork, so what is worth asserting is that they are addressed to
the engine ECU rather than the BMS, that the two-byte ``41 xx`` echo is
stripped correctly, and that the boundary values the standard specifies come
out right.
"""

from __future__ import annotations

import pytest
from elm.obd import assemble_isotp_messages, extract_payload, parse_frame_line
from vehicles.base import PidDefinition
from vehicles.kia_ceed_phev import (
    BMS,
    CELL_COUNT,
    ENGINE,
    FLAG_HV_CHARGING,
    FLAG_MAIN_RELAY,
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


ENGINE_RX = 0x7E8


def roundtrip_mode01(pid: int, payload: bytes) -> bytes:
    """Same path for a mode 01 response off the engine ECU.

    The echo is ``41 <pid>`` — also two bytes, but from a different service and
    a different header, so it is worth proving separately.
    """
    message = bytes([0x41, pid]) + payload
    frames = [f for line in isotp_lines(ENGINE_RX, message) if (f := parse_frame_line(line))]
    messages = assemble_isotp_messages(frames, rx_id=ENGINE_RX)
    return extract_payload(messages, 0x01, pid, 1)


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
    # Two ECUs, so two poll groups. Order matters: the coordinator ORs the
    # groups' wake results, but the BMS group must come first so the
    # best-corroborated response is what the transcript opens with.
    assert profile.headers == (BMS, ENGINE)


def test_profile_uses_service_21_with_one_byte_ids(profile):
    """The whole platform difference from E-GMP, asserted once.

    Only the HKMC rows: the mode 01 rows added for the ICE side are ordinary
    J1979 and are checked separately.
    """
    hkmc = [p for p in profile.pids if p.mode == 0x21]
    assert {p.pid_bytes for p in hkmc} == {1}
    assert {p.pid for p in hkmc} == {0x01, 0x05}
    assert {p.tx_header for p in hkmc} == {BMS}
    # Every row is either an HKMC service 21 row or a J1979 mode 01 row.
    assert {p.mode for p in profile.pids} == {0x21, 0x01}


def test_ice_rows_are_addressed_to_the_engine_ecu(profile):
    """A mode 01 request sent to 7E4 would be answered by the BMS, or not at
    all. The header, not the mode, is what routes these."""
    ice = [p for p in profile.pids if p.mode == 0x01]
    assert {p.tx_header for p in ice} == {ENGINE}
    assert {p.pid_bytes for p in ice} == {1}
    assert {p.pid for p in ice} == {0x2F, 0x5E, 0x05, 0x1F, 0x31}
    # 0x05 is a coolant temperature here and a BMS DID there. Same number,
    # different ECU, different service — this is exactly why the header is not
    # a detail.
    assert pid_by_key(profile, "coolant_temp").tx_header == ENGINE
    assert pid_by_key(profile, "soc_display").tx_header == BMS


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


def test_ac_port_bit_alone_does_not_mean_charging(profile):
    """Regression on a measured wrong answer.

    Bit 5 is the Ioniq table's "normal charge port", and this detector used to
    key on it. On the real car it stayed clear through a confirmed 2.9 kW AC
    charge, so it cannot be the deciding bit — and anything that reads it as
    one reports "not charging" while the car charges.
    """
    payload = roundtrip(0x01, build_2101(flags=FLAG_MAIN_RELAY | FLAG_NORMAL_CHARGE_PORT))
    values = {p.key: p.decode(payload) for p in profile.pids if p.pid == 0x01 and p.mode == 0x21}
    assert values["hv_current"] < 0
    assert profile.charging_detector(values) is False


def test_plugged_in_and_flowing_is_charging(profile):
    payload = roundtrip(0x01, build_2101(flags=FLAG_HV_CHARGING))
    values = {p.key: p.decode(payload) for p in profile.pids if p.pid == 0x01}
    assert profile.charging_detector(values) is True


def test_plugged_in_but_finished_is_not_charging(profile):
    """Port occupied, nothing flowing — a completed charge, not an active one."""
    p = bytearray(build_2101(flags=FLAG_HV_CHARGING))
    p[10:12] = (0).to_bytes(2, "big", signed=True)
    values = {
        pid.key: pid.decode(roundtrip(0x01, bytes(p))) for pid in profile.pids if pid.pid == 0x01
    }
    assert profile.charging_detector(values) is False


def test_charging_detector_declines_to_guess_without_flags(profile):
    assert profile.charging_detector({"hv_current": -13.8}) is None
    assert profile.charging_detector({}) is None


def test_hv_power_sign_is_negative_while_charging(profile):
    payload = roundtrip(0x01, build_2101(flags=FLAG_HV_CHARGING))
    values = {p.key: p.decode(payload) for p in profile.pids if p.pid == 0x01 and p.mode == 0x21}
    (power,) = profile.derived
    # 241.6 V x -13.8 A = -3.33 kW, i.e. ~3.3 kW into the pack — which is
    # exactly the Ceed PHEV's onboard AC charger rating.
    assert power.compute(values) == pytest.approx(-3.3341, abs=1e-3)


def test_derived_power_tolerates_a_missing_input(profile):
    (power,) = profile.derived
    assert power.compute({"hv_voltage": 241.6}) is None
    assert power.compute({}) is None


def test_only_measured_rows_are_marked_verified(profile):
    """Promotion to verified=True must track the capture, not optimism.

    Everything below is confirmed by the 2026-08-22 frame. Rows left False are
    either plausible-but-uncrosschecked (powers, operating time, motor speed at
    a standstill) or on DID 0x05, which has never been polled on this car.
    """
    assert {p.key for p in profile.pids if p.verified} == {
        "hv_voltage",
        "hv_current",
        "soc_bms",
        "battery_temp_max",
        "battery_temp_min",
        "battery_inlet_temp",
        "cell_voltage_max",
        "cell_voltage_min",
        "aux_voltage",
        "bms_flags",
        "cumulative_energy_charged",
        "cumulative_energy_discharged",
        "cumulative_charge_ah",
        "cumulative_discharge_ah",
        # ICE side: the two with a cross-check behind them. distance_since_clear
        # matched an independent ABRP log exactly; coolant_temp landed within
        # 2 °C of the pack sensors on a car whose engine had not run. Fuel
        # level, fuel rate and run time all read but none is corroborated.
        "coolant_temp",
        "distance_since_clear",
    }
    # DID 0x05 stays entirely unverified until that frame is actually read.
    assert not any(p.verified for p in profile.pids if p.mode == 0x21 and p.pid == 0x05)
    assert not pid_by_key(profile, "fuel_level").verified


def test_dash_soc_is_not_the_default_battery_sensor(profile):
    """DID 0x05's layout is contested three ways, so the headline battery
    number must come from the triple-corroborated DID 0x01 row."""
    assert pid_by_key(profile, "soc_bms").enabled_default is True
    assert pid_by_key(profile, "soc_display").enabled_default is False
    assert pid_by_key(profile, "soh").enabled_default is False


# --- Real capture, 2019 Ceed PHEV, 2026-08-22 13:09 local -------------------
# Lifted verbatim from the integration's diagnostics transcript with the car in
# READY, unplugged, sitting still. This is the regression anchor: the synthetic
# frames above prove the arithmetic, only these prove the byte map.
CAPTURE_2101 = [
    "7EC103D6101FFFFFFFF",
    "7EC21BB120116F80300",
    "7EC221A0F5C14131212",
    "7EC231213140014CD45",
    "7EC24CC0100FF8E0007",
    "7EC25E3A60007E50500",
    "7EC2602D3290002BA6E",
    "7EC2701CF3C106D018A",
    "7EC280000000003E800",
]


def capture_payload() -> bytes:
    frames = [f for line in CAPTURE_2101 if (f := parse_frame_line(line))]
    return extract_payload(assemble_isotp_messages(frames, rx_id=BMS_RX), MODE, 0x01, 1)


def test_real_capture_service_21_echo_is_stripped():
    """The frame announces 61 bytes: 2 of echo (61 01) plus 59 of payload."""
    assert len(capture_payload()) == 59


def test_real_capture_decoders(profile):
    payload = capture_payload()
    d = {p.key: p.decode(payload) for p in profile.pids if p.pid == 0x01}

    assert d["hv_voltage"] == pytest.approx(393.2)
    assert d["hv_current"] == pytest.approx(2.6)  # discharging into its own loads
    assert d["soc_bms"] == pytest.approx(93.5)
    assert d["battery_temp_max"] == pytest.approx(20.0)
    assert d["battery_temp_min"] == pytest.approx(19.0)
    assert d["aux_voltage"] == pytest.approx(14.2)
    assert d["cell_voltage_max"] == pytest.approx(4.10)
    assert d["cell_voltage_min"] == pytest.approx(4.08)
    assert d["cumulative_energy_charged"] == pytest.approx(18512.9)
    assert d["cumulative_energy_discharged"] == pytest.approx(17879.8)


def test_real_capture_cell_voltages_reconstruct_the_pack(profile):
    """96 cells x measured cell voltage must land on the pack voltage.

    Cell voltage and pack voltage live in different parts of the frame, so
    agreeing to within a percent by accident is not credible. This is also the
    check that refutes the Niro table's series count of 64, which would be 33 %
    low.
    """
    payload = capture_payload()
    cell = pid_by_key(profile, "cell_voltage_max").decode(payload)
    pack = pid_by_key(profile, "hv_voltage").decode(payload)
    assert 3.0 < cell < 4.3
    assert CELL_COUNT * cell == pytest.approx(pack, rel=0.01)
    assert 64 * cell != pytest.approx(pack, rel=0.05)


def test_real_capture_counters_imply_a_plausible_360v_pack(profile):
    """kWh / Ah must land on a sane average pack voltage, charge above discharge."""
    payload = capture_payload()
    d = {p.key: p.decode(payload) for p in profile.pids if p.pid == 0x01}
    v_charge = d["cumulative_energy_charged"] / d["cumulative_charge_ah"] * 1000
    v_discharge = d["cumulative_energy_discharged"] / d["cumulative_discharge_ah"] * 1000
    assert 320 < v_discharge < v_charge < 400


def test_real_capture_inlet_temp_is_near_the_cell_temps(profile):
    """Byte 22 is the offset E-GMP had to drop. Here it sits inside the pack's
    own temperature range, so it carries what the PHEV tables say it does."""
    payload = capture_payload()
    inlet = pid_by_key(profile, "battery_inlet_temp").decode(payload)
    t_min = pid_by_key(profile, "battery_temp_min").decode(payload)
    t_max = pid_by_key(profile, "battery_temp_max").decode(payload)
    assert t_min - 2 <= inlet <= t_max + 2


def test_real_capture_flag_byte_is_alive(profile):
    """Byte 9 reads 0x03 in READY: main relay closed while power flows, and
    neither charge-port bit set. On an EV6 the same byte reads 0x00."""
    flags = int(pid_by_key(profile, "bms_flags").decode(capture_payload()))
    assert flags == 0x03
    assert flags & FLAG_MAIN_RELAY
    assert not flags & FLAG_HV_CHARGING  # discharging, and not plugged in
    assert not flags & FLAG_NORMAL_CHARGE_PORT  # was not plugged in
    assert not flags & FLAG_RAPID_CHARGE_PORT  # AC-only car


def test_real_capture_is_not_reported_as_charging(profile):
    """The end-to-end case: awake, unplugged, current flowing. Must be False."""
    payload = capture_payload()
    values = {p.key: p.decode(payload) for p in profile.pids if p.pid == 0x01}
    assert profile.charging_detector(values) is False


# --- Mode 01, engine ECU ----------------------------------------------------
def test_mode01_echo_is_stripped(profile):
    """41 2F, not 41 2F xx. Same trap as service 21, different service."""
    payload = bytes(range(1, 8))
    assert roundtrip_mode01(0x2F, payload) == payload


def test_ice_decoders_match_the_standard(profile):
    """J1979 boundary values, one per row.

    These are not offsets to be discovered — the standard fixes them — so what
    is tested is that the right scale is applied to the right byte after the
    echo comes off.
    """
    fuel = pid_by_key(profile, "fuel_level")
    assert fuel.decode(roundtrip_mode01(0x2F, bytes([0]))) == pytest.approx(0.0)
    assert fuel.decode(roundtrip_mode01(0x2F, bytes([255]))) == pytest.approx(100.0)
    # The reading actually taken from the car: 178 -> 69.8 %.
    assert fuel.decode(roundtrip_mode01(0x2F, bytes([178]))) == pytest.approx(69.8, abs=0.05)

    rate = pid_by_key(profile, "engine_fuel_rate")
    assert rate.decode(roundtrip_mode01(0x5E, bytes([0x00, 0x00]))) == pytest.approx(0.0)
    # 0x0064 = 100 -> 5.00 L/h, a plausible warm-up burn.
    assert rate.decode(roundtrip_mode01(0x5E, bytes([0x00, 0x64]))) == pytest.approx(5.0)

    coolant = pid_by_key(profile, "coolant_temp")
    # A - 40, so the offset must survive: 0 is -40 °C, not 0 °C.
    assert coolant.decode(roundtrip_mode01(0x05, bytes([0]))) == pytest.approx(-40.0)
    assert coolant.decode(roundtrip_mode01(0x05, bytes([66]))) == pytest.approx(26.0)
    assert coolant.decode(roundtrip_mode01(0x05, bytes([130]))) == pytest.approx(90.0)

    runtime = pid_by_key(profile, "engine_run_time")
    assert runtime.decode(roundtrip_mode01(0x1F, bytes([0x01, 0x2C]))) == pytest.approx(300.0)

    distance = pid_by_key(profile, "distance_since_clear")
    # The two readings the ABRP cross-check was built from.
    assert distance.decode(roundtrip_mode01(0x31, (26301).to_bytes(2, "big"))) == 26301
    assert distance.decode(roundtrip_mode01(0x31, (26309).to_bytes(2, "big"))) == 26309


def test_engine_run_time_is_not_a_total(profile):
    """It resets to zero on every engine start. Declaring it total_increasing
    would make Home Assistant treat each start as a meter rollover."""
    assert pid_by_key(profile, "engine_run_time").state_class == "measurement"
    assert pid_by_key(profile, "distance_since_clear").state_class == "total_increasing"


def test_fuel_rows_are_enabled_by_default(profile):
    """Fuel consumption is the reason the ICE rows exist, so the two that feed
    it ship on. Run time is a diagnostic and ships off."""
    assert pid_by_key(profile, "fuel_level").enabled_default is True
    assert pid_by_key(profile, "engine_fuel_rate").enabled_default is True
    assert pid_by_key(profile, "coolant_temp").enabled_default is True
    assert pid_by_key(profile, "engine_run_time").enabled_default is False


def test_ice_rows_do_not_disturb_the_wake_probe(profile):
    """The coordinator probes each group with that group's first PID, so the
    BMS group must still open on the best-corroborated row.

    The ICE rows form their own group behind it, which also means a sleeping
    engine ECU cannot make the car look asleep while the BMS is answering.
    """
    assert profile.pids[0].key == "hv_voltage"
    assert profile.pids[0].mode == 0x21
    assert profile.pids_for_header(BMS)[0].key == "hv_voltage"
    assert profile.pids_for_header(ENGINE)[0].key == "fuel_level"
    assert all(p.mode == 0x21 for p in profile.pids_for_header(BMS))
    assert all(p.mode == 0x01 for p in profile.pids_for_header(ENGINE))
