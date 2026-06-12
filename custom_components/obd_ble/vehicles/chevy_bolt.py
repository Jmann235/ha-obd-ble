"""Chevrolet Bolt EV / EUV profile.

PID research credit: the community Bolt PID list maintained by Sean Graham
(https://allev.info/boltpids/, CC BY-NC-SA 2.5 CA), chevybolt.org forum
threads, and meatpiHQ/wican-fw issue #393. A small subset of those facts is
re-expressed here.

Header map (request -> response): 7E0->7E8 ECM, 7E1->7E9 motor/inverter,
7E4->7EC BECM (battery), 7E7->7EF BECM alt.

``verified=False`` rows are community-sourced and awaiting confirmation
against a real car via scripts/bench.py. Sign conventions for the two HV
current PIDs are explicitly part of that bench pass.
"""

from __future__ import annotations

from collections.abc import Mapping

from .base import DerivedDefinition, PidDefinition, VehicleProfile, s16, u8, u16, u32

ECM = 0x7E0
MOTOR = 0x7E1
BECM = 0x7E4
BECM_ALT = 0x7E7


def _common_pids() -> tuple[PidDefinition, ...]:
    return (
        # --- Battery state (BECM, 7E4) ---
        PidDefinition(
            key="soc",
            name="Battery",
            mode=0x22,
            pid=0x8334,
            tx_header=BECM,
            decode=lambda p: u8(p) * 100 / 255,
            unit="%",
            device_class="battery",
            verified=True,  # cross-confirmed on chevybolt.org
        ),
        PidDefinition(
            key="soc_raw",
            name="Battery raw",
            mode=0x22,
            pid=0x43AF,
            tx_header=BECM,
            decode=lambda p: u16(p) * 100 / 65535,
            unit="%",
            device_class="battery",
            enabled_default=False,
            verified=False,
        ),
        PidDefinition(
            key="battery_temp_avg",
            name="Battery temperature",
            mode=0x22,
            pid=0x434F,
            tx_header=BECM,
            decode=lambda p: u8(p) - 40,
            unit="°C",
            device_class="temperature",
            precision=0,
            verified=False,
        ),
        PidDefinition(
            key="battery_temp_max",
            name="Battery temperature max",
            mode=0x22,
            pid=0x4349,
            tx_header=BECM,
            decode=lambda p: u8(p) - 40,
            unit="°C",
            device_class="temperature",
            precision=0,
            enabled_default=False,
            verified=False,
        ),
        PidDefinition(
            key="battery_temp_min",
            name="Battery temperature min",
            mode=0x22,
            pid=0x434A,
            tx_header=BECM,
            decode=lambda p: u8(p) - 40,
            unit="°C",
            device_class="temperature",
            precision=0,
            enabled_default=False,
            verified=False,
        ),
        PidDefinition(
            key="battery_coolant_temp",
            name="Battery coolant temperature",
            mode=0x22,
            pid=0x41A4,
            tx_header=BECM,
            decode=lambda p: u8(p) - 40,
            unit="°C",
            device_class="temperature",
            precision=0,
            verified=False,
        ),
        PidDefinition(
            key="cell_voltage_min",
            name="Cell voltage min",
            mode=0x22,
            pid=0x4329,
            tx_header=BECM,
            decode=lambda p: u16(p) / 1666.666,
            unit="V",
            device_class="voltage",
            precision=3,
            enabled_default=False,
            verified=False,
        ),
        PidDefinition(
            key="cell_voltage_max",
            name="Cell voltage max",
            mode=0x22,
            pid=0x432B,
            tx_header=BECM,
            decode=lambda p: u16(p) / 1666.666,
            unit="V",
            device_class="voltage",
            precision=3,
            enabled_default=False,
            verified=False,
        ),
        PidDefinition(
            key="charge_cycles",
            name="Charge cycles",
            mode=0x22,
            pid=0x43A5,
            tx_header=BECM,
            decode=lambda p: float(u16(p)),
            unit=None,
            state_class="total_increasing",
            precision=0,
            icon="mdi:counter",
            enabled_default=False,
            verified=False,
        ),
        # --- Charging (BECM, 7E4) ---
        PidDefinition(
            key="charge_level",
            name="Charge level",
            mode=0x22,
            pid=0x4531,
            tx_header=BECM,
            decode=lambda p: float(u8(p)),
            unit=None,
            state_class=None,
            precision=0,
            icon="mdi:ev-station",
            verified=False,  # 0-3 enum; exact semantics to confirm at the car
        ),
        PidDefinition(
            key="ac_charge_voltage",
            name="AC charge voltage",
            mode=0x22,
            pid=0x4368,
            tx_header=BECM,
            decode=lambda p: u8(p) * 2,
            unit="V",
            device_class="voltage",
            precision=0,
            verified=False,
        ),
        PidDefinition(
            key="ac_charge_current",
            name="AC charge current",
            mode=0x22,
            pid=0x4369,
            tx_header=BECM,
            decode=lambda p: u8(p) * 0.2,
            unit="A",
            device_class="current",
            verified=False,
        ),
        PidDefinition(
            key="charger_voltage",
            name="Charger output voltage",
            mode=0x22,
            pid=0x436B,
            tx_header=BECM,
            decode=lambda p: u16(p) / 2,
            unit="V",
            device_class="voltage",
            precision=0,
            enabled_default=False,
            verified=False,
        ),
        PidDefinition(
            key="charger_current",
            name="Charger output current",
            mode=0x22,
            pid=0x436C,
            tx_header=BECM,
            decode=lambda p: s16(p) / 20,
            unit="A",
            device_class="current",
            enabled_default=False,
            verified=False,
        ),
        PidDefinition(
            key="last_charge_energy",
            name="Last charge energy",
            mode=0x22,
            pid=0x437F,
            tx_header=BECM,
            decode=lambda p: u16(p) * 10 / 1000,  # Wh -> kWh
            unit="kWh",
            device_class="energy",
            state_class=None,  # session stat; HA forbids energy+measurement
            precision=2,
            verified=False,
        ),
        PidDefinition(
            key="dcfc_current",
            name="DC fast charge current",
            mode=0x22,
            pid=0x4424,
            tx_header=BECM,
            decode=lambda p: (u16(p) - 32768) / 128,
            unit="A",
            device_class="current",
            enabled_default=False,
            verified=False,
        ),
        # --- Drive HV bus (motor controller, 7E1) ---
        PidDefinition(
            key="hv_voltage",
            name="HV battery voltage",
            mode=0x22,
            pid=0x2885,
            tx_header=MOTOR,
            decode=lambda p: u16(p) / 100,
            unit="V",
            device_class="voltage",
            verified=False,
        ),
        PidDefinition(
            key="hv_current",
            name="HV battery current",
            mode=0x22,
            pid=0x2414,
            tx_header=MOTOR,
            decode=lambda p: s16(p) / 20,
            unit="A",
            device_class="current",
            verified=False,  # sign convention (charge vs discharge) needs bench check
        ),
        # --- ECM (7E0) ---
        PidDefinition(
            key="ambient_temp",
            name="Ambient temperature",
            mode=0x22,
            pid=0x0046,
            tx_header=ECM,
            decode=lambda p: u8(p) - 40,
            unit="°C",
            device_class="temperature",
            precision=0,
            verified=False,
        ),
        PidDefinition(
            key="odometer",
            name="Odometer",
            mode=0x01,
            pid=0xA6,
            pid_bytes=1,
            tx_header=ECM,
            decode=lambda p: u32(p) / 10,
            unit="km",
            device_class="distance",
            state_class="total_increasing",
            verified=False,  # SAE PID, mandatory on MY2020+; older Bolts may refuse
        ),
        # --- BECM alt (7E7) ---
        PidDefinition(
            key="power_mode",
            name="Power mode",
            mode=0x22,
            pid=0x8002,
            tx_header=BECM_ALT,
            decode=lambda p: float(u8(p)),
            unit=None,
            state_class=None,
            precision=0,
            icon="mdi:car-electric",
            enabled_default=False,
            verified=False,
        ),
    )


def _hv_power(values: Mapping[str, float | None]) -> float | None:
    voltage = values.get("hv_voltage")
    current = values.get("hv_current")
    if voltage is None or current is None:
        return None
    return voltage * current / 1000


def _ac_charge_power(values: Mapping[str, float | None]) -> float | None:
    voltage = values.get("ac_charge_voltage")
    current = values.get("ac_charge_current")
    if voltage is None or current is None:
        return None
    return voltage * current / 1000


_DERIVED = (
    DerivedDefinition(
        key="hv_power",
        name="HV battery power",
        compute=_hv_power,
        unit="kW",
        inputs=("hv_voltage", "hv_current"),
        device_class="power",
        precision=1,
    ),
    DerivedDefinition(
        key="ac_charge_power",
        name="AC charge power",
        compute=_ac_charge_power,
        unit="kW",
        inputs=("ac_charge_voltage", "ac_charge_current"),
        device_class="power",
        precision=2,
    ),
)

_CHARGING_INPUTS = ("charge_level", "ac_charge_voltage", "ac_charge_current")


def _detect_charging(values: Mapping[str, float | None]) -> bool | None:
    level = values.get("charge_level")
    if level is not None:
        return level > 0
    ac_power = values.get("ac_charge_power")
    if ac_power is not None:
        return ac_power > 0.2
    ac_voltage = values.get("ac_charge_voltage")
    if ac_voltage is not None:
        return ac_voltage > 80
    return None


def _capacity_pid(pid: int, scale: float) -> PidDefinition:
    return PidDefinition(
        key="battery_capacity",
        name="Battery capacity",
        mode=0x22,
        pid=pid,
        tx_header=BECM,
        decode=lambda p: u16(p) / scale,
        unit="Ah",
        icon="mdi:battery-heart-variant",
        verified=False,
    )


CHEVY_BOLT_2017_2018 = VehicleProfile(
    key="chevy_bolt_2017_2018",
    name="Chevrolet Bolt EV (2017-2018)",
    manufacturer="Chevrolet",
    model="Bolt EV (2017-2018)",
    pids=(_capacity_pid(0x41A3, 10), *_common_pids()),
    derived=_DERIVED,
    charging_detector=_detect_charging,
    charging_inputs=_CHARGING_INPUTS,
)

CHEVY_BOLT_2019_PLUS = VehicleProfile(
    key="chevy_bolt_2019_plus",
    name="Chevrolet Bolt EV/EUV (2019+)",
    manufacturer="Chevrolet",
    model="Bolt EV/EUV (2019+)",
    pids=(_capacity_pid(0x45F9, 100), *_common_pids()),
    derived=_DERIVED,
    charging_detector=_detect_charging,
    charging_inputs=_CHARGING_INPUTS,
)
