"""Honda e:NP2: dashboard-verified SOC and odometer over 29-bit CAN.

Verified on an e:NP2 on 2026-11-01 using an iCar Pro 2S through an ESPHome
proxy. DID 202A returned 243 data bytes; B50 matched the dashboard's 81%.
DID 7022 returned 54 data bytes; B5..B8 were 00 00 23 67 = 9063 km,
independently confirmed on the dashboard. Offsets exclude the 62/DID echo.

Additional candidate fields are explicitly experimental and disabled by default.
Their regression values validate byte extraction, not physical sensor accuracy.
Adapter-measured 12V voltage remains available through the common ATRV sensor.
"""

from collections.abc import Mapping

from .base import DecodeFunc, DerivedDefinition, PidDefinition, VehicleProfile, s16, u8, u16, u32


def _candidate(
    key: str,
    name: str,
    decode: DecodeFunc,
    unit: str,
    device_class: str | None = None,
    precision: int = 1,
) -> PidDefinition:
    return PidDefinition(
        key=key,
        name=f"{name} (experimental)",
        mode=0x22,
        pid=0x202A,
        tx_header=0x18DA01F1,
        decode=decode,
        unit=unit,
        device_class=device_class,
        precision=precision,
        verified=False,
        enabled_default=False,
    )


def _hv_power(values: Mapping[str, float | None]) -> float | None:
    voltage, current = values.get("hv_voltage"), values.get("hv_current")
    if voltage is None or current is None:
        return None
    return voltage * current / 1000


# All offsets exclude the 62 20 2A echo. Voltage B55 (NOT B54) is a
# plausibility candidate; current follows the generic Honda-e map. Sign has
# not been checked while charging, so it must not drive charging detection.
# Temperature B59/B60 matches the hypothesized 202D array extrema (8..10 C),
# but neither scale has independent physical confirmation on this e:NP2.
# Capacity agrees with the 191.0 Ah e:Ny1 HDS example; it is NOT a SOH value.
_CANDIDATES = (
    _candidate("hv_voltage", "HV battery voltage", lambda p: u16(p, 55) / 20, "V", "voltage"),
    _candidate("hv_current", "HV battery current", lambda p: s16(p, 57) / 50, "A", "current"),
    _candidate(
        "battery_temp_max",
        "Battery temperature max",
        lambda p: float(u8(p, 59)),
        "°C",
        "temperature",
        0,
    ),
    _candidate(
        "battery_temp_min",
        "Battery temperature min",
        lambda p: float(u8(p, 60)),
        "°C",
        "temperature",
        0,
    ),
    _candidate("cell_soc_max", "Cell SOC max", lambda p: u16(p, 73) / 100, "%", precision=2),
    _candidate("cell_soc_min", "Cell SOC min", lambda p: u16(p, 75) / 100, "%", precision=2),
    _candidate("battery_capacity", "Battery capacity", lambda p: u16(p, 167) / 100, "Ah"),
)

HONDA_ENP2 = VehicleProfile(
    key="honda_enp2",
    name="Honda e:NP2",
    manufacturer="Honda",
    model="e:NP2",
    elm_protocol=7,
    vin_tx_header=0x18DA07F1,
    vin_did=0xF190,
    pids=(
        PidDefinition(
            key="soc",
            name="Battery",
            mode=0x22,
            pid=0x202A,
            tx_header=0x18DA01F1,
            decode=lambda p: float(u8(p, 50)),
            unit="%",
            device_class="battery",
            precision=0,
        ),
        PidDefinition(
            key="odometer",
            name="Odometer",
            mode=0x22,
            pid=0x7022,
            tx_header=0x18DA60F1,
            decode=lambda p: float(u32(p, 5)),
            unit="km",
            device_class="distance",
            state_class="total_increasing",
            precision=0,
        ),
        *_CANDIDATES,
    ),
    derived=(
        DerivedDefinition(
            key="hv_power",
            name="HV battery power (experimental)",
            compute=_hv_power,
            unit="kW",
            device_class="power",
            inputs=("hv_voltage", "hv_current"),
            enabled_default=False,
            precision=2,
        ),
    ),
)
