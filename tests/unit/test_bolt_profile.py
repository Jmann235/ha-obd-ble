"""Chevy Bolt profile: table validity and decoder math."""

from __future__ import annotations

import pytest
from vehicles.base import PidDefinition
from vehicles.registry import PROFILES, get_profile


def pid_by_key(profile, key: str) -> PidDefinition:
    return next(p for p in profile.pids if p.key == key)


def test_registry_profiles_validate():
    # Profile construction runs strict __post_init__ validation.
    assert set(PROFILES) == {"chevy_bolt_2017_2018", "chevy_bolt_2019_plus"}
    with pytest.raises(ValueError):
        get_profile("delorean")


@pytest.mark.parametrize("profile_key", sorted(PROFILES))
def test_headers_grouping(profile_key):
    profile = get_profile(profile_key)
    assert set(profile.headers) == {0x7E0, 0x7E1, 0x7E4, 0x7E7}
    for header in profile.headers:
        assert all(p.tx_header == header for p in profile.pids_for_header(header))


def test_soc_decode():
    profile = get_profile("chevy_bolt_2019_plus")
    soc = pid_by_key(profile, "soc")
    assert soc.decode(bytes([0xD5])) == pytest.approx(83.5, abs=0.1)
    assert soc.decode(bytes([0x00])) == 0
    assert soc.decode(bytes([0xFF])) == pytest.approx(100.0)


def test_capacity_decode_differs_by_model_year():
    old = pid_by_key(get_profile("chevy_bolt_2017_2018"), "battery_capacity")
    new = pid_by_key(get_profile("chevy_bolt_2019_plus"), "battery_capacity")
    assert old.pid == 0x41A3
    assert new.pid == 0x45F9
    # Same physical 144.5 Ah reading, different encodings.
    assert old.decode((1445).to_bytes(2, "big")) == pytest.approx(144.5)
    assert new.decode((14450).to_bytes(2, "big")) == pytest.approx(144.5)


def test_hv_current_is_signed():
    profile = get_profile("chevy_bolt_2019_plus")
    current = pid_by_key(profile, "hv_current")
    assert current.decode((-3000).to_bytes(2, "big", signed=True)) == pytest.approx(-150.0)
    assert current.decode((3000).to_bytes(2, "big")) == pytest.approx(150.0)


def test_temperature_offset():
    profile = get_profile("chevy_bolt_2019_plus")
    temp = pid_by_key(profile, "battery_temp_avg")
    assert temp.decode(bytes([40])) == 0
    assert temp.decode(bytes([65])) == 25


def test_odometer_decode():
    profile = get_profile("chevy_bolt_2019_plus")
    odometer = pid_by_key(profile, "odometer")
    assert odometer.mode == 0x01
    assert odometer.pid_bytes == 1
    assert odometer.decode((123456).to_bytes(4, "big")) == pytest.approx(12345.6)


def test_short_payload_raises():
    profile = get_profile("chevy_bolt_2019_plus")
    capacity = pid_by_key(profile, "battery_capacity")
    with pytest.raises(ValueError):
        capacity.decode(b"\x01")


def test_derived_power():
    profile = get_profile("chevy_bolt_2019_plus")
    derived = {d.key: d for d in profile.derived}
    values = {"hv_voltage": 360.0, "hv_current": -20.0}
    assert derived["hv_power"].compute(values) == pytest.approx(-7.2)
    assert derived["hv_power"].compute({"hv_voltage": 360.0, "hv_current": None}) is None
    assert derived["ac_charge_power"].compute(
        {"ac_charge_voltage": 240, "ac_charge_current": 30.0}
    ) == pytest.approx(7.2)


def test_charging_detector():
    profile = get_profile("chevy_bolt_2019_plus")
    assert profile.charging_detector({"charge_level": 2.0}) is True
    assert profile.charging_detector({"charge_level": 0.0}) is False
    assert (
        profile.charging_detector({"charge_level": None, "ac_charge_power": 7.2}) is True
    )
    assert profile.charging_detector({"charge_level": None, "ac_charge_power": None}) is None
    inputs = set(profile.charging_inputs)
    pid_keys = {p.key for p in profile.pids}
    assert inputs <= pid_keys


def test_derived_inputs_reference_real_pids():
    for profile in PROFILES.values():
        pid_keys = {p.key for p in profile.pids}
        for derived in profile.derived:
            assert set(derived.inputs) <= pid_keys, derived.key
