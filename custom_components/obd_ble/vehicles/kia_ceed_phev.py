"""Kia Ceed PHEV (8.9 kWh) profile — likely also Niro PHEV and Ioniq PHEV.

PID research credit: JejuSoul/OBD-PIDs-for-HKMC-EVs, specifically the
``Kia Niro PHEV - 8.9kWh`` and ``Ioniq PHEV - 8.9kWh`` tables. A small subset
of those facts is re-expressed here.

Header map (request -> response): 7E4->7EC BMS, 7E0->7E8 engine ECU.

⚠️ SERVICE 21, NOT 22. Where E-GMP answers ``220101``, the 8.9 kWh PHEV
platform is documented on ``2101`` — service 0x21 (the legacy HKMC
ReadDataByLocalIdentifier) with a one-byte local id. The data layout behind
it is the same; only the request and the echo length differ, and
``ObdSession.query`` derives both from ``mode``/``pid_bytes``, so no
transport change was needed. If the Ceed's BMS answers ``2101`` with NRC 0x11
(service not supported), the fallback is mode 0x22 / pid 0x0101 /
``pid_bytes=2`` and nothing else in this file changes.

CONFIRMED against a real 2019 Ceed PHEV on 2026-08-22, by decoding the raw
``2101`` frame out of the integration's diagnostics transcript with the car in
READY and unplugged. Two independent cross-checks say the DID 0x01 byte map is
right rather than merely plausible:

* max cell voltage read 4.10 V and 96 cells x 4.10 V = 393.6 V against a
  measured pack voltage of 393.2 V — 0.1 % apart, from two offsets that sit in
  different parts of the frame.
* the cumulative counters imply average pack voltages of 358 V charging and
  346 V discharging (kWh / Ah) — correct magnitudes for a 360 V nominal pack,
  and in the right order, since charging sits above discharging.

A third, weaker check: byte 29 read 14.2 V against the adapter's own ``ATRV``
reading of 14.5 V on the same rail, measured through a completely separate
path.

DID 0x05 is still unconfirmed — its rows are disabled by default, so the
frame is never polled and nothing here has seen it.

What remains unverified is flagged per row. The corroboration behind each
offset before the bench pass split sharply by frame:

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
where they came from — and both are now confirmed alive on the Ceed:

* battery inlet temperature at byte 22 read 20 °C against cell temperatures of
  19-20 °C. On an EV6 the same byte read 75 °C against 30-32 °C.
* byte 9 read 0x03 with the HV system live and nothing plugged in, and 0x83
  while the car drew 2.9 kW from the wallbox. On an EV6 that byte reads 0x00
  even while charging. Bits 0 and 1 are set in both states; bit 7 is the only
  one that moved, so bit 7 is what the charging detector keys on. Bit 5 —
  which the Ioniq table labels the normal (AC) charge port — stayed CLEAR
  through that entire confirmed AC charge, so it is *not* the AC-port bit on
  this car. See ``_detect_charging`` for what that settles and what it does
  not. The byte ships raw as ``bms_flags`` so it stays inspectable, and the
  DC-port bit reads 0 as it must on an AC-only car.

THE ICE SIDE
------------
A plug-in hybrid burns petrol, so a profile that stops at the BMS describes
half the car. Unlike everything above, these rows are not HKMC-specific: they
are plain SAE J1979 mode 01 PIDs on the engine ECU at 7E0, and each decode is
fixed by the standard rather than reverse-engineered from a Torque table.

What *is* car-specific is which of them this ECU answers, and that was
enumerated rather than assumed. The three supported-PID bitmasks read

    0100 -> BE3FA813    0120 -> 801FB015    0140 -> FEDC8C85

i.e. 48 supported PIDs, among them 0x2F (fuel tank level) and 0x5E (engine
fuel rate) — the two that matter for running-cost tracking. The Ceed also
answers 0902 (VIN), which an EV6 does not, so the engine ECU really is there
and really is talking.

Measured on 2026-08-22, engine cold and not running:

* ``0131`` distance since codes cleared read 26301 km and then 26309 km after
  two short trips. An independent ABRP log of those same two trips recorded
  4 km + 4 km. Exact agreement between two unrelated systems, so this row is
  marked verified.
* ``0105`` coolant temperature read 21-26 °C over the afternoon against
  battery module temperatures of 22-24 °C, on a car whose engine had not
  started. Right magnitude, and it tracks ambient, which confirms the A-40
  decode. No hot-engine sample yet, but a wrong offset or scale could not
  land inside 2 °C of the pack sensors by luck.
* ``015E`` fuel rate and ``011F`` engine run time both read exactly 0 with the
  engine off. Correct, but it only proves the PIDs answer — neither scale
  factor has been exercised against a running engine, so both stay unverified.
* ``012F`` fuel level is the awkward one. It read 178/255 = 69.8 % while the
  dashboard showed a nearly full tank and the car's own cloud range estimate
  implied roughly 39 L in a 37 L tank. The raw byte also threw one 197 reading
  between two 178s, which no stationary tank does. So the byte is being read,
  but neither its span nor its stability is established: shipped unverified,
  and anything downstream should calibrate against litres actually pumped
  rather than treating this as a tank percentage.
"""

from __future__ import annotations

from collections.abc import Mapping

from .base import DerivedDefinition, PidDefinition, VehicleProfile, s8, s16, u8, u16, u32

BMS = 0x7E4
ENGINE = 0x7E0  # standard J1979 engine ECU, for the mode 01 rows

# Service 21 local identifiers on the BMS.
DID_BMS_MAIN = 0x01  # pack electricals, temps, flags, cumulative counters
DID_BMS_AUX = 0x05  # deterioration / SOH / dash SOC — layout unsettled

# Bits within byte 9 of DID 0x01. Names from the Ioniq PHEV table; the
# comments are what this car actually did. Two states have been measured:
# 0x03 in READY, unplugged, 1.0 kW leaving the pack; 0x83 while taking 2.9 kW
# from the wallbox.
FLAG_MAIN_RELAY = 0x01  # bit 0 — set in both states, as it must be
FLAG_UNKNOWN_BIT1 = 0x02  # bit 1 — set in both states; meaning unestablished
FLAG_NORMAL_CHARGE_PORT = 0x20  # bit 5 — clear even mid-AC-charge; see _detect_charging
FLAG_RAPID_CHARGE_PORT = 0x40  # bit 6 — never set; the Ceed PHEV has no DC port
FLAG_HV_CHARGING = 0x80  # bit 7 — the bit that actually followed charging

# 8.9 kWh pack, 96 cells in series -> 360 V nominal. MEASURED: 96 x 4.10 V =
# 393.6 V against a pack reading of 393.2 V (0.1 %), and the cumulative
# counters imply a 346-358 V average.
#
# The Niro PHEV table says 64, computing average cell voltage as pack/64 with a
# 204-275.2 V range. That is a 240 V, 64-cell pack — i.e. the Niro *HEV*
# figures, which that table was derived from and never corrected; its own
# README lists "adjusting values to match PHEV specs" as an open TODO. 64 cells
# would put this car 33 % off. Do not take the series count from that table.
CELL_COUNT = 96


def _detect_charging(values: Mapping[str, float | None]) -> bool | None:
    """A PHEV cannot use the EV6's rule. Current sign alone is not enough.

    On a BEV, current into the pack means the wallbox. On a PHEV it also means
    regenerative braking *and* the engine driving the HSG as a generator — a
    Ceed in hybrid mode charges its own HV battery down the motorway. Ship the
    EV6 detector here and the car reports "charging" for half of every trip.

    So a flag bit decides and the current sign only corroborates. Byte 9 is
    ALIVE on this platform, unlike E-GMP, and two measured states pin down
    which bit to use:

    * 0x03 — READY, unplugged, 1.0 kW leaving the pack.
    * 0x83 — plugged in, 2.9 kW entering the pack from the wallbox.

    Bit 7 is the only bit that moved, so bit 7 is the charging signal. Bit 5,
    which the Ioniq PHEV table labels the normal (AC) charge port, was clear
    through the whole of that confirmed AC charge. Keying on it — the obvious
    reading of the table, and what this function did first — reports "not
    charging" while the car charges, so do not restore it.

    What bit 7 *means* is still open. "Charge port energised" and "HV battery
    is being charged from any source" both fit the two samples, and they differ
    during a drive: the second would also go high under regen and under HSG
    charging, which is exactly the false positive this detector exists to
    avoid. Two things bound the damage — the adapter is only in BLE range
    while the car is parked, so driving states are rarely sampled at all, and
    ``bms_flags`` ships raw so the byte stays inspectable. The clean fix, once
    the drive-motor-speed offset is confirmed, is to require a stationary
    motor here as well.

    REGRESSION, 2026-08-23: this used to also require ``hv_current < -0.2``
    after the flag check, on the theory that current only "corroborates".
    That is wrong and was caught by a live miss, not a review: the first poll
    after plugging in read flags=0x80 (bit 7 set, genuinely charging) and
    hv_current=0.0 (the AC charger had not ramped up yet), and the AND turned
    a real charging start into a reported "not charging". Bit 7 is the only
    measured signal for this state; requiring a second, unrelated measurement
    to also clear before believing it just adds a race the flag alone doesn't
    have. So the flag is the entire answer once present — current is kept
    only as an exposed, inspectable value, never as a gate.
    """
    flags = values.get("bms_flags")
    if flags is None:
        # No flag byte, no way to separate wall charging from engine/regen.
        return None
    return bool(int(flags) & FLAG_HV_CHARGING)


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
        verified=True,
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
        #
        # The offset and scaling are confirmed: +2.6 A at 393.2 V gave 1.02 kW
        # for a car sitting in READY, which is the right order for its own
        # electronics. Only the discharge direction has been observed so far;
        # the negative-while-charging case is inferred from the shared HKMC
        # convention, not yet measured on this car.
        decode=lambda p: s16(p, 10) / 10,
        unit="A",
        device_class="current",
        verified=True,
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
        verified=True,
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
        verified=True,
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
        verified=True,
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
        verified=True,
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
        verified=True,
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
        verified=True,
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
        verified=True,
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
        verified=True,
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
        verified=True,
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
        verified=True,
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
        verified=True,
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
        verified=True,
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
        # different quantity, and the offset E-GMP uses for SOH.
        #
        # Do NOT read across platforms here. Decoding a real EV6 0105 frame at
        # @35 gives 0x0000: that payload is 43 bytes and bytes 35-38 fall in a
        # trailing run of nulls, so @35 is simply not a field on E-GMP and says
        # nothing about the EV6's own suspicious exactly-100.0 % at @25.
        # Whether @35 is real *here* is still open — this frame has not been
        # polled on the Ceed, because this row ships disabled.
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
    # --- Mode 01 on the engine ECU: the ICE side ---
    # Plain SAE J1979, so the decodes come from the standard rather than from a
    # Torque table. All five confirmed answering on this car; the module
    # docstring records which reading backs which row, and why only two of them
    # are marked verified.
    PidDefinition(
        key="fuel_level",
        name="Fuel level",
        mode=0x01,
        pid=0x2F,
        pid_bytes=1,
        tx_header=ENGINE,
        # J1979 defines this as A * 100 / 255. Whether the sender actually
        # spans the byte is a separate question, and on this car it appears not
        # to: 178 read against a nearly full 37 L tank. Treat the movement as
        # meaningful and the absolute percentage as uncalibrated.
        decode=lambda p: u8(p, 0) * 100 / 255,
        unit="%",
        icon="mdi:gas-station",
        precision=1,
        verified=False,
    ),
    PidDefinition(
        key="engine_fuel_rate",
        name="Engine fuel rate",
        mode=0x01,
        pid=0x5E,
        pid_bytes=1,
        tx_header=ENGINE,
        # J1979: (256A + B) / 20 L/h. Read 0.00 with the engine off, which says
        # the PID answers but exercises neither the scale nor the offset.
        decode=lambda p: u16(p, 0) / 20,
        unit="L/h",
        icon="mdi:fuel",
        precision=2,
        verified=False,
    ),
    PidDefinition(
        key="coolant_temp",
        name="Engine coolant temperature",
        mode=0x01,
        pid=0x05,
        pid_bytes=1,
        tx_header=ENGINE,
        # J1979: A - 40 °C. On a PHEV this is the cold-start detector that
        # matters — cabin heat comes from the engine, so below roughly 15 °C
        # ambient the ICE fires in the driveway before the car has moved.
        decode=lambda p: float(u8(p, 0) - 40),
        unit="°C",
        device_class="temperature",
        precision=0,
    ),
    PidDefinition(
        key="engine_run_time",
        name="Engine run time",
        mode=0x01,
        pid=0x1F,
        pid_bytes=1,
        tx_header=ENGINE,
        # J1979: (256A + B) seconds since this engine start. Resets to 0 every
        # start, so it is a measurement, not a total.
        decode=lambda p: float(u16(p, 0)),
        unit="s",
        device_class="duration",
        precision=0,
        icon="mdi:engine-outline",
        enabled_default=False,
        verified=False,
    ),
    PidDefinition(
        key="distance_since_clear",
        name="Distance since codes cleared",
        mode=0x01,
        pid=0x31,
        pid_bytes=1,
        tx_header=ENGINE,
        # J1979: (256A + B) km. Not the odometer — a DTC clear resets it — but
        # it is the only distance this dongle can see, and trip deltas taken
        # from it matched an independent ABRP log exactly (see the docstring).
        decode=lambda p: float(u16(p, 0)),
        unit="km",
        device_class="distance",
        state_class="total_increasing",
        precision=0,
        icon="mdi:map-marker-distance",
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

# bms_flags carries the charging bit, hv_current only corroborates it.
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
