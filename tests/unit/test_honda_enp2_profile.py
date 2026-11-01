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
    assert {p.key for p in profile.pids} == {"soc", "odometer"}
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


@pytest.mark.parametrize("key,length", [("soc", 50), ("odometer", 8)])
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
