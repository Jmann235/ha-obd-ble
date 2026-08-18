"""Kia EV6 (E-GMP) profile — also applicable to Hyundai Ioniq 5/6 and EV9.

PID research credit: the OVMS Hyundai Ioniq 5 vehicle component
(openvehicles/Open-Vehicle-Monitoring-System-3, ``hif_can_poll.cpp``) and
JejuSoul/OBD-PIDs-for-HKMC-EVs. The two agree byte-for-byte on the frames
used here; a small subset of those facts is re-expressed below.

Header map (request -> response): 7E4->7EC BMS. Everything in this profile
lives on the BMS, so there is a single poll group.

All byte offsets are into the payload *after* the ``62 01 xx`` service echo,
which is exactly what ``ObdSession.query`` returns.

Both frames are multi-frame ISO-TP: ``220101`` answers in 9 CAN frames
(62 bytes), ``220105`` in 7 (46 bytes). A vLinker MC+ handles both on the
ELM's automatic flow control, over an ESPHome Bluetooth proxy, with no
``ATFCSH``/``ATFCSD`` setup needed.

CONFIRMED against a real 2022 EV6 Long Range on 2026-08-16, by decoding the
raw frames out of the integration's diagnostics transcript. Two independent
cross-checks say the byte map is right rather than merely plausible:

* max/min cell voltage both read 3.74 V, and 192 cells x 3.74 V = 718 V
  against a measured pack voltage of 719.0 V.
* the cumulative counters imply average pack voltages of 752 V charging and
  734 V discharging (kWh / Ah) — correct magnitudes for an 800 V pack, and
  in the right order, since charging sits above discharging.

Two offsets from the Kona/Niro tables did NOT carry over to E-GMP and were
dropped rather than shipped wrong: battery inlet temperature at byte 22, and
the HV charging flag at byte 9. See the comments at each site.
"""

from __future__ import annotations

from collections.abc import Mapping

from .base import DerivedDefinition, PidDefinition, VehicleProfile, s8, s16, u8, u16, u32

BMS = 0x7E4

# Service 22 DIDs on the BMS.
DID_BMS_MAIN = 0x0101  # pack electricals, temps, cumulative counters
DID_BMS_AUX = 0x0105  # SOH and the dashboard SOC


def _detect_charging(values: Mapping[str, float | None]) -> bool | None:
    """Charging is decided by the SIGN OF PACK CURRENT, not by a status bit.

    The Kona/Niro tables put a 'HV charging' flag at bit 7 of byte 9, and on
    an EV6 that byte reads 0x00 even with 1.3 kW flowing into the pack — the
    main-relay bit in the same byte is zero too, which it cannot be while
    power flows. So byte 9 is simply not that byte on E-GMP, and the flag was
    dropped rather than shipped wrong.

    Current sign is unambiguous anyway: the DC-DC converter draws *from* the
    pack, so anything feeding the HV battery shows as negative. The deadband
    only rejects sensor noise around zero — a real charge was observed at
    -1.4 A, so it has to stay tight.
    """
    current = values.get("hv_current")
    if current is None:
        return None
    return current < -0.2


def _hv_power(values: Mapping[str, float | None]) -> float | None:
    voltage = values.get("hv_voltage")
    current = values.get("hv_current")
    if voltage is None or current is None:
        return None
    return voltage * current / 1000


_PIDS: tuple[PidDefinition, ...] = (
    # --- 220101: pack electricals, temperatures, lifetime counters ---
    # This frame leads the table deliberately: the group's first PID is the
    # probe that decides "is the BMS awake?", and 220101 is the more
    # universally documented of the two DIDs.
    PidDefinition(
        key="hv_voltage",
        name="HV battery voltage",
        mode=0x22,
        pid=DID_BMS_MAIN,
        tx_header=BMS,
        decode=lambda p: u16(p, 12) / 10,
        unit="V",
        device_class="voltage",
        verified=True,
    ),
    PidDefinition(
        key="hv_current",
        name="HV battery current",
        mode=0x22,
        pid=DID_BMS_MAIN,
        tx_header=BMS,
        # Sign is OBD-native: positive = discharge, negative = charge.
        decode=lambda p: s16(p, 10) / 10,
        unit="A",
        device_class="current",
        verified=True,
    ),
    PidDefinition(
        key="soc_bms",
        name="Battery raw",
        mode=0x22,
        pid=DID_BMS_MAIN,
        tx_header=BMS,
        # The BMS's own SOC, on a wider window than the dashboard figure.
        # Enabled by default: it is the fallback if 220105 turns out
        # unsupported, and the dash-vs-BMS gap is worth watching in its own
        # right.
        decode=lambda p: u8(p, 4) * 0.5,
        unit="%",
        device_class="battery",
        verified=True,
    ),
    PidDefinition(
        key="battery_temp_max",
        name="Battery temperature max",
        mode=0x22,
        pid=DID_BMS_MAIN,
        tx_header=BMS,
        decode=lambda p: float(s8(p, 14)),
        unit="°C",
        device_class="temperature",
        precision=0,
        verified=True,
    ),
    PidDefinition(
        key="battery_temp_min",
        name="Battery temperature min",
        mode=0x22,
        pid=DID_BMS_MAIN,
        tx_header=BMS,
        decode=lambda p: float(s8(p, 15)),
        unit="°C",
        device_class="temperature",
        precision=0,
        verified=True,
    ),
    # NOTE: the Kona/Niro tables put battery inlet temperature at byte 22.
    # On an EV6 that byte read 0x4B = 75 °C while the module temperatures at
    # 16-20 read a consistent 30-32 °C, so the offset does not carry over to
    # E-GMP. Omitted rather than shipped wrong.
    PidDefinition(
        key="aux_voltage",
        name="12V battery voltage",
        mode=0x22,
        pid=DID_BMS_MAIN,
        tx_header=BMS,
        decode=lambda p: u8(p, 29) * 0.1,
        unit="V",
        device_class="voltage",
        verified=True,
    ),
    PidDefinition(
        key="cell_voltage_max",
        name="Cell voltage max",
        mode=0x22,
        pid=DID_BMS_MAIN,
        tx_header=BMS,
        decode=lambda p: u8(p, 23) / 50,
        unit="V",
        device_class="voltage",
        precision=2,
        enabled_default=False,
        verified=True,
    ),
    PidDefinition(
        key="cell_voltage_min",
        name="Cell voltage min",
        mode=0x22,
        pid=DID_BMS_MAIN,
        tx_header=BMS,
        decode=lambda p: u8(p, 25) / 50,
        unit="V",
        device_class="voltage",
        precision=2,
        enabled_default=False,
        verified=True,
    ),
    # Monotonic battery-side counters straight from the BMS. Not a rolling
    # window, so unlike the cloud's 90-day sums these work as utility_meter
    # sources. ⚠️ NOT lifetime-since-new: on a 103,256 km EV6 they read
    # 3,974.8 kWh discharged, i.e. 3.85 kWh/100 km, which is impossible. The
    # epoch is unknown (a BMS or 12 V service would explain it). Use the
    # DELTAS; do not derive lifetime consumption from the absolute values.
    PidDefinition(
        key="cumulative_energy_charged",
        name="Cumulative energy charged",
        mode=0x22,
        pid=DID_BMS_MAIN,
        tx_header=BMS,
        decode=lambda p: u32(p, 38) / 10,
        unit="kWh",
        device_class="energy",
        state_class="total_increasing",
        verified=True,
    ),
    PidDefinition(
        key="cumulative_energy_discharged",
        name="Cumulative energy discharged",
        mode=0x22,
        pid=DID_BMS_MAIN,
        tx_header=BMS,
        decode=lambda p: u32(p, 42) / 10,
        unit="kWh",
        device_class="energy",
        state_class="total_increasing",
        verified=True,
    ),
    PidDefinition(
        key="cumulative_charge_ah",
        name="Cumulative charge",
        mode=0x22,
        pid=DID_BMS_MAIN,
        tx_header=BMS,
        decode=lambda p: u32(p, 30) / 10,
        unit="Ah",
        state_class="total_increasing",
        enabled_default=False,
        verified=True,
    ),
    PidDefinition(
        key="cumulative_discharge_ah",
        name="Cumulative discharge",
        mode=0x22,
        pid=DID_BMS_MAIN,
        tx_header=BMS,
        decode=lambda p: u32(p, 34) / 10,
        unit="Ah",
        state_class="total_increasing",
        enabled_default=False,
        verified=True,
    ),
    # --- 220105: SOH and the SOC the dashboard actually shows ---
    PidDefinition(
        key="soc",
        name="Battery",
        mode=0x22,
        pid=DID_BMS_AUX,
        tx_header=BMS,
        decode=lambda p: u8(p, 31) * 0.5,
        unit="%",
        device_class="battery",
        verified=True,
    ),
    # ⚠️ The one row NOT confirmed. It decodes to exactly 100.0 % on a
    # 103,256 km car, which is suspiciously round: an independent
    # ABRP-over-OBD capacity check on the same pack implies ~1.3 % of
    # ageing, i.e. ~98.7 %. Either the field is coarse/placeholder on
    # E-GMP, or byte 25 is not SOH here. Left in because it is harmless
    # and someone with a more worn pack can settle it in one reading.
    PidDefinition(
        key="soh",
        name="Battery health",
        mode=0x22,
        pid=DID_BMS_AUX,
        tx_header=BMS,
        decode=lambda p: u16(p, 25) / 10,
        unit="%",
        icon="mdi:battery-heart-variant",
        verified=False,
    ),
)

_DERIVED: tuple[DerivedDefinition, ...] = (
    DerivedDefinition(
        key="hv_power",
        name="HV battery power",
        compute=_hv_power,
        unit="kW",
        # Sign follows hv_current: positive = discharge, negative = charge.
        inputs=("hv_voltage", "hv_current"),
        device_class="power",
        precision=2,
    ),
)

_CHARGING_INPUTS = ("hv_current",)


KIA_EV6 = VehicleProfile(
    key="kia_ev6",
    name="Kia EV6 (E-GMP)",
    manufacturer="Kia",
    model="EV6",
    pids=_PIDS,
    derived=_DERIVED,
    charging_detector=_detect_charging,
    charging_inputs=_CHARGING_INPUTS,
)
