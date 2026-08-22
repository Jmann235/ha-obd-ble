"""Kia Ceed PHEV (8.9 kWh) profile — likely also Niro PHEV and Ioniq PHEV.

PID research credit: JejuSoul/OBD-PIDs-for-HKMC-EVs, specifically the
``Kia Niro PHEV - 8.9kWh`` and ``Ioniq PHEV - 8.9kWh`` tables. A small subset
of those facts is re-expressed here.

Header map (request -> response): 7E4->7EC BMS. Single poll group.

⚠️ SERVICE 21, NOT 22. Where E-GMP answers ``220101``, the 8.9 kWh PHEV
platform is documented on ``2101`` — service 0x21 (the legacy HKMC
ReadDataByLocalIdentifier) with a one-byte local id. The data layout behind
it is the same; only the request and the echo length differ, and
``ObdSession.query`` derives both from ``mode``/``pid_bytes``, so no
transport change was needed. If the Ceed's BMS answers ``2101`` with NRC 0x11
(service not supported), the fallback is mode 0x22 / pid 0x0101 /
``pid_bytes=2`` and nothing else in this file changes.

NOTHING HERE IS CONFIRMED ON A CEED. Every row is ``verified=False``. What
is known is how well-corroborated each row is, and that splits sharply by
frame:

* **DID 0x01 — strong.** The Niro PHEV and Ioniq PHEV tables agree on all 30
  rows they share, and the 12 of those that also exist in the E-GMP profile
  match it byte-for-byte, sign and width included — and that profile was
  confirmed against a real 2022 EV6. So three sources agree and one of them
  was measured. These offsets are very likely right.
* **DID 0x05 — weak, and the sources conflict.** The two PHEV tables share
  only 4 rows there, and disagree about the interesting ones. SOH is
  ``u16@35`` in the Ioniq table but ``u16@25`` in the Niro table (named
  "Maximum Deterioration" there, which is not the same quantity). Display SOC
  is ``u8@38`` in the Ioniq table, absent from the Niro one, and ``u8@31`` on
  E-GMP. Three platforms, three answers. Treat 0x05 as unmapped until bench.

Because of that split, the primary battery sensor here is the BMS SOC from
DID 0x01 (triple-corroborated), not the dashboard SOC from 0x05. The dash
figure ships disabled by default so a wrong offset cannot become the most
visible number on the card.

Byte offsets are into the payload *after* the ``61 01`` service echo, which
is what ``ObdSession.query`` returns.

Two offsets that E-GMP had to drop are kept here, because this platform is
where they came from: battery inlet temperature at byte 22, and the flag byte
at byte 9. On an EV6 byte 9 read 0x00 with 1.3 kW flowing; on a PHEV it is
documented as carrying the charge-port and main-relay bits. Byte 9 is exposed
raw as ``bms_flags`` precisely so one glance at the diagnostics settles it.
"""

from __future__ import annotations

from collections.abc import Mapping

from .base import DerivedDefinition, PidDefinition, VehicleProfile, s8, s16, u8, u16, u32

BMS = 0x7E4

# Service 21 local identifiers on the BMS.
DID_BMS_MAIN = 0x01  # pack electricals, temps, flags, cumulative counters
DID_BMS_AUX = 0x05  # deterioration / SOH / dash SOC — layout unsettled

# Bits within byte 9 of DID 0x01, per the Ioniq PHEV table.
FLAG_MAIN_RELAY = 0x01  # bit 0
FLAG_NORMAL_CHARGE_PORT = 0x20  # bit 5 — AC. The Ceed PHEV has no DC port.
FLAG_RAPID_CHARGE_PORT = 0x40  # bit 6 — must stay 0 on this car; free sanity check
FLAG_HV_CHARGING = 0x80  # bit 7

# 8.9 kWh pack, 64 cells in series (the Niro table computes average cell
# voltage as pack/64, and its 204-275.2 V range is 64 x 3.19-4.30 V). This is
# the number the bench cross-check multiplies by.
CELL_COUNT = 64


def _detect_charging(values: Mapping[str, float | None]) -> bool | None:
    """A PHEV cannot use the EV6's rule. Current sign alone is not enough.

    On a BEV, current into the pack means the wallbox. On a PHEV it also means
    regenerative braking *and* the engine driving the HSG as a generator — a
    Ceed in hybrid mode charges its own HV battery down the motorway. Ship the
    EV6 detector here and the car reports "charging" for half of every trip.

    So the plug decides, not the current: byte 9 bit 5 is the AC charge port,
    which is semantically exactly the question being asked. Current sign is
    kept only as a corroborating check that something is actually flowing in.

    If byte 9 turns out dead on the Ceed the way it is on E-GMP, this returns
    False forever rather than True wrongly — a missing sensor, not a lying
    one. ``bms_flags`` is exposed so that shows up as an obvious 0 instead of
    a mystery. Bench check: read bms_flags unplugged, then plugged.
    """
    flags = values.get("bms_flags")
    if flags is None:
        # No flag byte, no way to separate wall charging from engine/regen.
        return None
    plugged = int(flags) & FLAG_NORMAL_CHARGE_PORT
    if not plugged:
        return False
    current = values.get("hv_current")
    if current is None:
        return True
    return current < -0.2


def _hv_power(values: Mapping[str, float | None]) -> float | None:
    voltage = values.get("hv_voltage")
    current = values.get("hv_current")
    if voltage is None or current is None:
        return None
    return voltage * current / 1000


_PIDS: tuple[PidDefinition, ...] = (
    # --- DID 0x01: pack electricals, temperatures, flags, counters ---
    # Leads the table deliberately: the group's first PID is the probe that
    # decides "is the BMS awake?", and this is the better-corroborated DID.
    PidDefinition(
        key="hv_voltage",
        name="HV battery voltage",
        mode=0x21,
        pid=DID_BMS_MAIN,
        pid_bytes=1,
        tx_header=BMS,
        decode=lambda p: u16(p, 12) / 10,
        unit="V",
        device_class="voltage",
        verified=False,
    ),
    PidDefinition(
        key="hv_current",
        name="HV battery current",
        mode=0x21,
        pid=DID_BMS_MAIN,
        pid_bytes=1,
        tx_header=BMS,
        # Sign is OBD-native: positive = discharge, negative = into the pack.
        # On a PHEV "into the pack" is not necessarily the wallbox — see
        # _detect_charging.
        decode=lambda p: s16(p, 10) / 10,
        unit="A",
        device_class="current",
        verified=False,
    ),
    PidDefinition(
        key="soc_bms",
        name="Battery",
        mode=0x21,
        pid=DID_BMS_MAIN,
        pid_bytes=1,
        tx_header=BMS,
        # The BMS's own SOC. Primary battery sensor on this profile because
        # DID 0x05's dash SOC offset is contested three ways; this one is
        # agreed by both PHEV tables and confirmed on E-GMP.
        decode=lambda p: u8(p, 4) * 0.5,
        unit="%",
        device_class="battery",
        verified=False,
    ),
    PidDefinition(
        key="battery_temp_max",
        name="Battery temperature max",
        mode=0x21,
        pid=DID_BMS_MAIN,
        pid_bytes=1,
        tx_header=BMS,
        decode=lambda p: float(s8(p, 14)),
        unit="°C",
        device_class="temperature",
        precision=0,
        verified=False,
    ),
    PidDefinition(
        key="battery_temp_min",
        name="Battery temperature min",
        mode=0x21,
        pid=DID_BMS_MAIN,
        pid_bytes=1,
        tx_header=BMS,
        decode=lambda p: float(s8(p, 15)),
        unit="°C",
        device_class="temperature",
        precision=0,
        verified=False,
    ),
    PidDefinition(
        key="battery_inlet_temp",
        name="Battery inlet temperature",
        mode=0x21,
        pid=DID_BMS_MAIN,
        pid_bytes=1,
        tx_header=BMS,
        # Byte 22. E-GMP had to drop this one (it read 75 °C while the module
        # sensors read 30-32 °C), but this platform is where the offset comes
        # from, so it is kept. Bench check: it must sit within a few degrees
        # of battery_temp_min/max, not tens.
        decode=lambda p: float(s8(p, 22)),
        unit="°C",
        device_class="temperature",
        precision=0,
        verified=False,
    ),
    PidDefinition(
        key="cell_voltage_max",
        name="Cell voltage max",
        mode=0x21,
        pid=DID_BMS_MAIN,
        pid_bytes=1,
        tx_header=BMS,
        decode=lambda p: u8(p, 23) / 50,
        unit="V",
        device_class="voltage",
        precision=2,
        enabled_default=False,
        verified=False,
    ),
    PidDefinition(
        key="cell_voltage_min",
        name="Cell voltage min",
        mode=0x21,
        pid=DID_BMS_MAIN,
        pid_bytes=1,
        tx_header=BMS,
        decode=lambda p: u8(p, 25) / 50,
        unit="V",
        device_class="voltage",
        precision=2,
        enabled_default=False,
        verified=False,
    ),
    PidDefinition(
        key="aux_voltage",
        name="12V battery voltage",
        mode=0x21,
        pid=DID_BMS_MAIN,
        pid_bytes=1,
        tx_header=BMS,
        decode=lambda p: u8(p, 29) * 0.1,
        unit="V",
        device_class="voltage",
        verified=False,
    ),
    PidDefinition(
        key="bms_flags",
        name="BMS flags",
        mode=0x21,
        pid=DID_BMS_MAIN,
        pid_bytes=1,
        tx_header=BMS,
        # Raw byte 9. Not pretty, but it is the whole charge-port question in
        # one number, and the charging detector reads it. Diagnostic: enabled
        # is off, but the coordinator polls it anyway because it is in
        # charging_inputs.
        decode=lambda p: float(u8(p, 9)),
        unit=None,
        state_class=None,
        precision=0,
        icon="mdi:flag-outline",
        enabled_default=False,
        verified=False,
    ),
    PidDefinition(
        key="max_charge_power",
        name="Max charge power",
        mode=0x21,
        pid=DID_BMS_MAIN,
        pid_bytes=1,
        tx_header=BMS,
        decode=lambda p: u16(p, 5) / 100,
        unit="kW",
        device_class="power",
        enabled_default=False,
        verified=False,
    ),
    PidDefinition(
        key="max_discharge_power",
        name="Max discharge power",
        mode=0x21,
        pid=DID_BMS_MAIN,
        pid_bytes=1,
        tx_header=BMS,
        decode=lambda p: u16(p, 7) / 100,
        unit="kW",
        device_class="power",
        enabled_default=False,
        verified=False,
    ),
    # Monotonic battery-side counters. As on E-GMP these are good
    # utility_meter sources but are NOT guaranteed lifetime-since-new — use
    # the deltas, not the absolute values.
    PidDefinition(
        key="cumulative_energy_charged",
        name="Cumulative energy charged",
        mode=0x21,
        pid=DID_BMS_MAIN,
        pid_bytes=1,
        tx_header=BMS,
        decode=lambda p: u32(p, 38) / 10,
        unit="kWh",
        device_class="energy",
        state_class="total_increasing",
        verified=False,
    ),
    PidDefinition(
        key="cumulative_energy_discharged",
        name="Cumulative energy discharged",
        mode=0x21,
        pid=DID_BMS_MAIN,
        pid_bytes=1,
        tx_header=BMS,
        decode=lambda p: u32(p, 42) / 10,
        unit="kWh",
        device_class="energy",
        state_class="total_increasing",
        verified=False,
    ),
    PidDefinition(
        key="cumulative_charge_ah",
        name="Cumulative charge",
        mode=0x21,
        pid=DID_BMS_MAIN,
        pid_bytes=1,
        tx_header=BMS,
        decode=lambda p: u32(p, 30) / 10,
        unit="Ah",
        state_class="total_increasing",
        enabled_default=False,
        verified=False,
    ),
    PidDefinition(
        key="cumulative_discharge_ah",
        name="Cumulative discharge",
        mode=0x21,
        pid=DID_BMS_MAIN,
        pid_bytes=1,
        tx_header=BMS,
        decode=lambda p: u32(p, 34) / 10,
        unit="Ah",
        state_class="total_increasing",
        enabled_default=False,
        verified=False,
    ),
    PidDefinition(
        key="operating_time",
        name="BMS operating time",
        mode=0x21,
        pid=DID_BMS_MAIN,
        pid_bytes=1,
        tx_header=BMS,
        decode=lambda p: u32(p, 46) / 3600,
        unit="h",
        device_class="duration",
        state_class="total_increasing",
        precision=0,
        enabled_default=False,
        verified=False,
    ),
    PidDefinition(
        key="motor_speed",
        name="Drive motor speed",
        mode=0x21,
        pid=DID_BMS_MAIN,
        pid_bytes=1,
        tx_header=BMS,
        decode=lambda p: float(s16(p, 53)),
        unit="rpm",
        precision=0,
        icon="mdi:engine-outline",
        enabled_default=False,
        verified=False,
    ),
    # --- DID 0x05: deterioration / SOH / dash SOC — SOURCES CONFLICT ---
    # Everything below is a coin-flip between two tables. Kept because a
    # single bench read settles all of it, and shipped disabled so a wrong
    # offset stays invisible until then.
    PidDefinition(
        key="soc_display",
        name="Battery (dash)",
        mode=0x21,
        pid=DID_BMS_AUX,
        pid_bytes=1,
        tx_header=BMS,
        # u8@38 per the Ioniq PHEV table. The Niro table has no dash SOC at
        # all and E-GMP puts it at byte 31. Disabled by default: if this
        # offset is wrong it would otherwise become the headline number.
        decode=lambda p: u8(p, 38) * 0.5,
        unit="%",
        device_class="battery",
        enabled_default=False,
        verified=False,
    ),
    PidDefinition(
        key="soh",
        name="Battery health",
        mode=0x21,
        pid=DID_BMS_AUX,
        pid_bytes=1,
        tx_header=BMS,
        # u16@35 per the Ioniq PHEV table, which names it "State of Health".
        # The Niro table instead has "Maximum Deterioration" at u16@25 — a
        # different quantity, and notably the offset E-GMP used for SOH and
        # got a suspicious exactly-100.0 %. Worth settling here: if @35 gives
        # a believable non-round number on the Ceed, that is also evidence
        # the EV6 profile's soh row is reading the wrong field.
        decode=lambda p: u16(p, 35) / 10,
        unit="%",
        icon="mdi:battery-heart-variant",
        enabled_default=False,
        verified=False,
    ),
    PidDefinition(
        key="cell_voltage_deviation",
        name="Cell voltage deviation",
        mode=0x21,
        pid=DID_BMS_AUX,
        pid_bytes=1,
        tx_header=BMS,
        # The one DID 0x05 row both PHEV tables agree on.
        decode=lambda p: u8(p, 20) / 50,
        unit="V",
        device_class="voltage",
        precision=2,
        enabled_default=False,
        verified=False,
    ),
)

_DERIVED: tuple[DerivedDefinition, ...] = (
    DerivedDefinition(
        key="hv_power",
        name="HV battery power",
        compute=_hv_power,
        unit="kW",
        # Sign follows hv_current: positive = discharge, negative = into pack.
        inputs=("hv_voltage", "hv_current"),
        device_class="power",
        precision=2,
    ),
)

# bms_flags is the plug signal, hv_current only corroborates it.
_CHARGING_INPUTS = ("bms_flags", "hv_current")


KIA_CEED_PHEV = VehicleProfile(
    key="kia_ceed_phev",
    name="Kia Ceed PHEV (8.9 kWh)",
    manufacturer="Kia",
    model="Ceed PHEV",
    pids=_PIDS,
    derived=_DERIVED,
    charging_detector=_detect_charging,
    charging_inputs=_CHARGING_INPUTS,
)
