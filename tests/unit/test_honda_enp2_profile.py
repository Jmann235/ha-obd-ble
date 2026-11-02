"""Decode real e:NP2 captures through the same ISO-TP path as HA."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from elm.obd import (
    ObdDecodeError,
    assemble_isotp_messages,
    extract_payload,
    parse_frame_line,
    response_id,
)
from vehicles.registry import get_profile


@pytest.fixture
def capture():
    return json.loads((Path(__file__).parents[1] / "fixtures" / "honda_enp2.json").read_text())


def decode_capture(profile, key, text):
    pid = next(p for p in profile.pids if p.key == key)
    frames = [frame for line in text.splitlines() if (frame := parse_frame_line(line))]
    messages = assemble_isotp_messages(frames, rx_id=response_id(pid.tx_header))
    payload = extract_payload(messages, pid.mode, pid.pid, pid.pid_bytes)
    return payload, pid.decode(payload)


def test_registered_profile():
    profile = get_profile("honda_enp2")
    assert profile.elm_protocol == 7
    assert profile.vin_tx_header == 0x18DA07F1
    assert profile.vin_did == 0xF190
    assert profile.headers == (0x18DA01F1, 0x18DA60F1)
    assert {p.key for p in profile.pids if p.enabled_default} == {"soc", "odometer", "range"}
    candidates = [p for p in profile.pids if not p.verified]
    assert len(candidates) == 7
    assert all(not p.enabled_default and "experimental" in p.name for p in candidates)
    assert profile.charging_detector({"soc": 81}) is None


@pytest.mark.parametrize("compact", [False, True])
def test_dashboard_verified_soc_capture(capture, compact):
    text = capture["soc"]["response"]
    if compact:
        text = text.replace(" ", "")
    payload, value = decode_capture(get_profile("honda_enp2"), "soc", text)
    assert len(payload) == 243  # Padding excluded; sequence wraps twice.
    assert value == 81


def test_dashboard_verified_odometer_capture(capture):
    payload, value = decode_capture(
        get_profile("honda_enp2"), "odometer", capture["odometer"]["response"]
    )
    assert len(payload) == 54
    assert payload[5:9] == bytes.fromhex("00002367")
    assert value == 9063


def test_odometer_uses_full_32_bit_counter():
    pid = next(p for p in get_profile("honda_enp2").pids if p.key == "odometer")
    assert pid.decode(bytes(5) + (100_000).to_bytes(4, "big")) == 100_000
    assert pid.device_class == "distance"
    assert pid.state_class == "total_increasing"


@pytest.mark.parametrize("key,length", [("soc", 50), ("odometer", 8), ("range", 15)])
def test_short_payloads_are_rejected(key, length):
    pid = next(p for p in get_profile("honda_enp2").pids if p.key == key)
    with pytest.raises((ValueError, IndexError)):
        pid.decode(bytes(length))


def test_other_ecu_cannot_supply_soc(capture):
    text = capture["soc"]["response"].replace("18 DA F1 01", "18 DA F1 16")
    with pytest.raises(ObdDecodeError, match="no positive response"):
        decode_capture(get_profile("honda_enp2"), "soc", text)


def test_invalid_profile_protocol():
    with pytest.raises(ValueError, match="unsupported CAN protocol"):
        replace(get_profile("honda_enp2"), elm_protocol=0)


@pytest.mark.parametrize(
    "key,expected",
    [
        ("hv_voltage", 383.6),
        ("hv_current", 1.4),
        ("battery_temp_max", 10),
        ("battery_temp_min", 8),
        ("cell_soc_max", 79.76),
        ("cell_soc_min", 78.33),
        ("battery_capacity", 191.0),
    ],
)
def test_candidate_extraction_from_live_capture(capture, key, expected):
    # Locks byte offsets against the actual response, not physical accuracy.
    _, value = decode_capture(get_profile("honda_enp2"), key, capture["research_202a"]["response"])
    assert value == pytest.approx(expected)


@pytest.mark.parametrize(
    "key,length",
    [
        ("hv_voltage", 56),
        ("hv_current", 58),
        ("battery_temp_max", 59),
        ("battery_temp_min", 60),
        ("cell_soc_max", 74),
        ("cell_soc_min", 76),
        ("battery_capacity", 168),
    ],
)
def test_candidate_truncated_payload_rejected(key, length):
    pid = next(p for p in get_profile("honda_enp2").pids if p.key == key)
    with pytest.raises((ValueError, IndexError)):
        pid.decode(bytes(length))


def test_experimental_power_requires_both_inputs():
    power = next(d for d in get_profile("honda_enp2").derived if d.key == "hv_power")
    assert not power.enabled_default
    assert power.inputs == ("hv_voltage", "hv_current")
    assert power.compute({"hv_voltage": 383.6}) is None
    assert power.compute({"hv_voltage": None, "hv_current": 1.4}) is None
    assert power.compute({"hv_voltage": 383.6, "hv_current": 1.4}) == pytest.approx(0.53704)
    # Signed current is preserved without asserting which sign means charging.
    assert power.compute({"hv_voltage": 383.6, "hv_current": -1.4}) == pytest.approx(-0.53704)


@pytest.mark.parametrize("fixture_key,expected", [("range_167", 167), ("range_166", 166)])
@pytest.mark.parametrize("compact", [False, True])
def test_range_live_captures(capture, fixture_key, expected, compact):
    text = capture[fixture_key]["response"]
    if compact:
        text = text.replace(" ", "")
    payload, value = decode_capture(get_profile("honda_enp2"), "range", text)
    assert len(payload) == 54
    assert value == expected
    pid = next(p for p in get_profile("honda_enp2").pids if p.key == "range")
    assert pid.enabled_default
    assert pid.device_class == "distance"
    assert pid.state_class == "measurement"  # Range may rise or fall; not an odometer.


def test_range_filters_other_ecu(capture):
    text = capture["range_166"]["response"].replace("18 DA F1 60", "18 DA F1 01")
    with pytest.raises(ObdDecodeError, match="no positive response"):
        decode_capture(get_profile("honda_enp2"), "range", text)
