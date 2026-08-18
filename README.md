# OBD BLE Vehicle Monitor for Home Assistant

Poll a car over a Bluetooth LE OBD-II adapter and expose battery state,
temperatures, charging rates, and odometer as Home Assistant entities. Built
for the **Chevrolet Bolt EV/EUV** with a **Veepeak OBDCheck BLE+** adapter;
the design is vehicle-profile based, so more cars can be added as data
tables rather than code.

The car only needs to be **in BLE range when you want data**: the
integration tracks the adapter's advertisements, polls when it appears, and
holds the last-known values (with explicit freshness entities) when the car
drives away or its modules sleep.

## How it works

```text
HA bluetooth stack (local radio or ESPHome proxy)
        │  advertisement presence → poll trigger
        ▼
elm/transport.py   BLE GATT serial (notify+write characteristic pair)
elm/elm327.py      AT init: ATE0 L0 S0 H1 SP6 AT1 · ATRV 12V readout
elm/obd.py         mode 01/09/22 queries · ATSH/ATCRA per ECU · ISO-TP reassembly
vehicles/…         per-car PID tables (hex, scaling, units, HA metadata)
coordinator.py     presence-aware polling · idle vs charging cadence
sensor / binary_sensor → one HA device per car (VIN as serial)
```

- **Polling cadence**: idle default 5 min, while charging 1 min (both
  configurable per car in Options). The first PID of each ECU group probes
  for a sleeping module so idle cycles stay short.
- **Multiple cars**: add one config entry per adapter; each picks its own
  vehicle profile.
- **Long-term statistics** work out of the box (proper device/state
  classes); no extra dashboard stack needed.

## Requirements

- Home Assistant 2025.1+ with the Bluetooth integration set up (a local
  adapter or an [ESPHome Bluetooth proxy](https://esphome.io/components/bluetooth_proxy.html)
  in range of where the car parks).
- A BLE ELM327-compatible OBD adapter. Tested target: Veepeak OBDCheck
  BLE+ (service `FFF0`). Vgate iCar Pro BLE and LeLink-style (`FFE0`)
  adapters are recognized too; anything exposing a notify+write GATT pair
  should work.
- A supported vehicle profile:
  - Chevrolet Bolt EV (2017–2018)
  - Chevrolet Bolt EV/EUV (2019+)

## Install

**HACS (custom repository):** HACS → Integrations → ⋮ → Custom
repositories → add `https://github.com/Jmann235/ha-obd-ble` with category
*Integration* → install → restart HA. Updates are delivered through GitHub
releases and show up in HACS like any other integration update.

**Manual:** copy `custom_components/obd_ble/` into your HA `config/custom_components/`
and restart.

Plug the adapter into the OBD-II port (driver footwell). When the adapter
advertises in range, HA shows a discovery prompt; otherwise add it via
*Settings → Devices & services → Add integration → OBD BLE Vehicle Monitor*
(pick the discovered adapter or type its MAC, then choose the vehicle
profile).

### If no devices appear in the picker

The device dropdown is intentionally **unfiltered**: it lists every
Bluetooth advertisement Home Assistant can currently see (likely OBD
adapters sorted first, with RSSI), so it doubles as a Bluetooth health
check:

- **Empty list** → Home Assistant's Bluetooth stack sees *nothing at all*.
  Check *Settings → Devices & services → Bluetooth*: is there an adapter?
  On HA OS in a VM (e.g. Proxmox) there is **no radio by default** — pass a
  USB Bluetooth dongle through to the HA VM or deploy an
  [ESPHome Bluetooth proxy](https://esphome.io/components/bluetooth_proxy.html)
  near where the car parks.
- **Other devices listed but no adapter** → Bluetooth works; the adapter
  isn't advertising in range. Confirm it is plugged in and powered, nothing
  else (e.g. a phone OBD app) is connected to it, and it is BLE — most cheap
  ELM327 dongles are *Classic* Bluetooth, which BLE scanning can never see.
  Verify with a phone BLE scanner (nRF Connect) next to the car.

## Entities (Bolt profile)

| Entity | Source | Notes |
|---|---|---|
| Battery (%) | `228334` @7E4 | dash-displayed SOC |
| Battery raw (%) | `2243AF` @7E4 | high-resolution SOC (disabled by default) |
| Battery capacity (Ah) | `2241A3` / `2245F9` @7E4 | PID differs by model year — pick the right profile |
| Battery temperature avg/max/min (°C) | `22434F`/`224349`/`22434A` @7E4 | max/min disabled by default |
| Battery coolant temperature (°C) | `2241A4` @7E4 | |
| Cell voltage min/max (V) | `224329`/`22432B` @7E4 | disabled by default |
| HV battery voltage / current / power | `222885`/`222414` @7E1 + derived | sign convention pending bench check |
| AC charge voltage / current / power | `224368`/`224369` @7E4 + derived | L1/L2 charging |
| Charger output voltage / current | `22436B`/`22436C` @7E4 | disabled by default |
| DC fast charge current (A) | `224424` @7E4 | disabled by default |
| Last charge energy (kWh) | `22437F` @7E4 | |
| Charge level | `224531` @7E4 | 0–3 enum, drives charging detection |
| Charge cycles | `2243A5` @7E4 | disabled by default |
| Odometer (km) | mode `01` PID `A6` @7E0 | SAE standard; may be unsupported on pre-2020 cars |
| Ambient temperature (°C) | `220046` @7E0 | |
| 12V battery voltage | adapter `ATRV` | works whenever the adapter is powered |
| Adapter in range / Car awake / Charging | binary sensors | freshness + automation hooks |
| Last successful poll | timestamp | diagnostic |

PID research credit: [Sean Graham's community Bolt PID list](https://allev.info/boltpids/)
(CC BY-NC-SA 2.5 CA), chevybolt.org forums, and the WiCAN project.

## Entities (Kia EV6 / E-GMP profile)

Profile key `kia_ev6`. Should apply unchanged to the Hyundai Ioniq 5/6 and
Kia EV9 — same platform, same BMS DIDs — but has only been read against an
EV6 so far. Everything lives on the BMS at header `7E4`, so there is a
single poll group and two multi-frame reads per cycle.

| Entity | Source | Notes |
| --- | --- | --- |
| Battery (%) | `220105` @7E4 byte 31 | dash-displayed SOC, 0.5 % steps |
| Battery raw (%) | `220101` @7E4 byte 4 | the BMS's own wider-window SOC |
| Battery health (%) | `220105` @7E4 byte 25 | **unconfirmed** — see below |
| HV battery voltage / current | `220101` bytes 12 / 10 | current is signed, **negative = charging** |
| HV battery power (kW) | derived | V x I, same sign convention |
| Battery temperature max/min (°C) | `220101` bytes 14 / 15 | |
| Cell voltage max/min (V) | `220101` bytes 23 / 25 | disabled by default |
| 12V battery voltage | `220101` byte 29 | the BMS's reading, distinct from the adapter's `ATRV` |
| Cumulative energy charged/discharged (kWh) | `220101` bytes 38 / 42 | monotonic, **not** lifetime — see below |
| Cumulative charge/discharge (Ah) | `220101` bytes 30 / 34 | disabled by default |

Byte offsets are into the payload *after* the `62 01 xx` echo. Confirmed
against a 2022 EV6 Long Range by decoding raw frames from the integration's
own diagnostics transcript, and cross-checked two ways that are hard to pass
by accident:

- max and min cell voltage both read 3.74 V, and 192 cells x 3.74 V = 718 V
  against a measured pack voltage of 719.0 V;
- the cumulative counters imply average pack voltages of 752 V charging and
  734 V discharging (kWh / Ah) — right magnitude for an 800 V pack, and in
  the right order.

**Two Kona/Niro offsets do not carry over to E-GMP** and are deliberately
absent rather than shipped wrong:

- *battery inlet temperature at byte 22* read 75 °C while the module
  temperatures at bytes 16-20 read a consistent 30-32 °C;
- *the HV charging flag at bit 7 of byte 9* read `0x00` with 1.3 kW flowing
  into the pack — and the main-relay bit in that same byte cannot be zero
  while power flows, so byte 9 is not that byte here. Charging detection uses
  the **sign of pack current** instead, which needs no bit archaeology.

**Two caveats worth reading before trusting a number:**

- *Battery health* decodes to exactly 100.0 % on a 103,256 km car. An
  independent capacity check on the same pack implies ~1.3 % of ageing. Either
  the field is coarse on E-GMP or byte 25 is not SOH. Marked `verified=False`;
  a reading from a more worn pack would settle it.
- *The cumulative counters are monotonic but not lifetime-since-new.* On that
  same car they imply 3.85 kWh/100 km, which is impossible. Something reset
  them. They are perfectly good `utility_meter` sources — use the deltas, not
  the absolute values.

PID research credit: the [OVMS Hyundai Ioniq 5 component](https://github.com/openvehicles/Open-Vehicle-Monitoring-System-3)
(`hif_can_poll.cpp`) and [JejuSoul/OBD-PIDs-for-HKMC-EVs](https://github.com/JejuSoul/OBD-PIDs-for-HKMC-EVs).

## Dashboard card ("Garage Hero")

The integration ships a custom Lovelace card — no HACS frontend repo or
manual resource setup; it's auto-loaded once the integration is installed.
Design source: the Claude Design handoff in the
[EV_Logger repo](https://github.com/Jmann235/EV_Logger/tree/main/design/home-assistant-obd)
(Direction C, "Garage Hero").

```yaml
type: custom:obd-ble-card
name: Chevy Bolt
image: /local/bolt.png            # drop your car render in config/www/
layout: hero                      # hero | wide | compact
gauge: segments                   # segments | ring | bar
accent: "#2dd4bf"
units: mi                         # mi | km (entities in km are converted)
charge_limit: 80                  # % target/limit line
max_range: 259                    # full-charge range (used when no range entity)
usable_kwh: 65                    # usable pack kWh (kWh-now + time-to-limit)
capacity_new: 180                 # optional: new-pack Ah -> shows Health %
charger_label: "Garage · Level 2" # shown in the sheet when not derivable
entities:
  soc: sensor.chevy_bolt_battery
  charging: binary_sensor.chevy_bolt_charging
  charge_power: sensor.chevy_bolt_ac_charge_power
  odometer: sensor.chevy_bolt_odometer
  capacity: sensor.chevy_bolt_battery_capacity
  ac_voltage: sensor.chevy_bolt_ac_charge_voltage
  ac_current: sensor.chevy_bolt_ac_charge_current
  # range: sensor.optional_range_entity
maintenance:
  - name: Tire rotation
    icon: tire                    # tire | drop | brake | filter | spark | battery | car
    interval_miles: 7500
    interval_months: 12
    last_miles: 24000
    last_date: "2026-01-15"
  - name: Cabin air filter
    icon: filter
    interval_miles: 22500
    interval_months: 24
    last_miles: 12000
    last_date: "2025-06-01"
  - name: Battery coolant
    icon: drop
    interval_miles: 150000
    interval_months: 60
    last_miles: 0
    last_date: "2023-01-10"
```

- **Tile**: photo header with charging pill (kW) and name/odometer, big
  estimated-range numeral, battery gauge (segmented blinks while charging;
  ring pulses; bar has animated stripes + a charge-limit tick), charging
  status line, and the most-urgent service item.
- **Tap the tile** → slide-up service sheet: charging box (rate, AC V/A,
  time-to-limit), capacity/health, and every maintenance item with an
  urgency ring — red under 15% of interval left, amber under 40%.
- **Maintenance math**: remaining = `last + interval − odometer` and
  `last_date + interval_months`, displayed as "X mi or {date}" — whichever
  comes first drives the urgency color, matching the design.
- Estimated range uses your `range` entity if you have one, else
  `soc × max_range`.

## Verifying PIDs against your car (bench tool)

Rows marked `verified=False` in
[chevy_bolt.py](custom_components/obd_ble/vehicles/chevy_bolt.py) come from
community research and should be confirmed once against your actual car —
no Home Assistant needed, any laptop with Bluetooth works:

```bash
pip install bleak bleak-retry-connector
python scripts/bench.py scan                       # find the adapter MAC
python scripts/bench.py sweep --address <MAC> --profile chevy_bolt_2019_plus --state asleep
python scripts/bench.py sweep --address <MAC> --profile chevy_bolt_2019_plus --state on
python scripts/bench.py sweep --address <MAC> --profile chevy_bolt_2019_plus --state charging
python scripts/bench.py repl --address <MAC>       # raw ELM327 console
```

Each sweep prints raw payload + decoded value per PID and writes
`transcripts/<state>.jsonl`. Things to settle from the three runs:

1. Which PIDs answer in each car state (asleep / on / charging).
2. Sign convention of `hv_current` (charge vs discharge) — fix the decode
   in the profile if inverted.
3. `charge_level` semantics (expected 0 = idle, 1/2 = L1/L2, 3 = DC).
4. Odometer support on your model year.

The same information is available later from a running install via the
device's **Download diagnostics** (includes the raw ELM transcript).

## Sleep behavior, in practice

- Car off and locked: Bolt modules sleep after a few minutes — HV PIDs
  return `NO DATA` (held values + `Car awake: off`), and the Veepeak
  eventually stops advertising entirely (`Adapter in range: off`). This is
  normal and intentional; the adapter wakes with the car.
- Charging keeps the BECM awake: SOC, temps, and charge power update on
  the fast interval through the whole session.
- The adapter's own `ATRV` 12 V reading works any time the adapter is
  awake, even when the car's ECUs are asleep.

## Development

```bash
pip install -r requirements_dev.txt
ruff check .
pytest tests/unit            # driver + profile tests, no HA needed
pip install -r requirements_test.txt
pytest tests/components      # config flow tests under the HA harness
```

Adding a vehicle = one new module in
`custom_components/obd_ble/vehicles/` (a `VehicleProfile` with a PID
table) plus a registry entry. The bench tool's `sweep`/`repl` are the
way to map PIDs on a new car.

## License

MIT (see [LICENSE](LICENSE)). PID tables credit the community sources noted
above.
