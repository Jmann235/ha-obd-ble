"""Standalone at-the-car validation tool — no Home Assistant required.

Run from the repo root on any machine with Bluetooth (Windows included):

    pip install bleak bleak-retry-connector
    python scripts/bench.py scan
    python scripts/bench.py sweep --address AA:BB:CC:DD:EE:FF \
        --profile chevy_bolt_2019_plus --state charging
    python scripts/bench.py repl --address AA:BB:CC:DD:EE:FF

``sweep`` queries every PID in the profile (including disabled-by-default
ones), printing raw payload + decoded value, and writes a JSONL transcript
to transcripts/<state>.jsonl. Run it three times — car asleep, car on, and
while charging — and keep the transcripts: they become test fixtures and
settle every ``verified=False`` formula in the profile.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
# Make `elm` and `vehicles` importable without Home Assistant: inside HA they
# are subpackages of obd_ble; here the obd_ble directory itself goes on the
# path so its HA-importing __init__.py never runs.
sys.path.insert(0, str(REPO_ROOT / "custom_components" / "obd_ble"))

from bleak import BleakScanner  # noqa: E402
from elm.elm327 import Elm327, ElmError, NoDataError  # noqa: E402
from elm.obd import ObdSession  # noqa: E402
from elm.transport import ADVERTISED_SERVICE_UUIDS, BleSerialTransport  # noqa: E402
from vehicles.registry import PROFILES, get_profile  # noqa: E402


def make_transport(address: str) -> BleSerialTransport:
    async def find_device():
        return await BleakScanner.find_device_by_address(address, timeout=15.0)

    return BleSerialTransport(find_device, name=address)


async def cmd_scan(_args: argparse.Namespace) -> int:
    print("Scanning 10s for BLE devices...")
    devices = await BleakScanner.discover(timeout=10.0, return_adv=True)
    known = {u.lower() for u in ADVERTISED_SERVICE_UUIDS}
    rows = []
    for device, adv in devices.values():
        uuids = {u.lower() for u in (adv.service_uuids or [])}
        is_obd = bool(uuids & known) or "obd" in (device.name or "").lower() or "veepeak" in (
            device.name or ""
        ).lower()
        rows.append((is_obd, adv.rssi, device.address, device.name or "?", sorted(uuids)))
    rows.sort(key=lambda r: (not r[0], -r[1]))
    for is_obd, rssi, address, name, uuids in rows:
        marker = ">>" if is_obd else "  "
        print(f"{marker} {address}  rssi={rssi:>4}  {name}  {uuids if is_obd else ''}")
    print("\n>> = looks like an OBD adapter")
    return 0


async def cmd_sweep(args: argparse.Namespace) -> int:
    profile = get_profile(args.profile)
    transport = make_transport(args.address)
    elm = Elm327(transport)
    session = ObdSession(elm)
    results: list[dict] = []

    print(f"Connecting to {args.address} ...")
    await transport.connect()
    try:
        banner = await elm.initialize()
        print(f"Adapter: {banner or '(no banner)'}")

        voltage = await elm.voltage()
        print(f"ATRV 12V rail: {voltage} V")
        results.append({"kind": "atrv", "value": voltage})

        vin = await session.read_vin()
        print(f"VIN: {vin or '(no response — car asleep?)'}")
        results.append({"kind": "vin", "value": vin})

        for pid in profile.pids:
            label = f"{pid.mode:02X}{pid.pid:0{pid.pid_bytes * 2}X}@{pid.tx_header:03X}"
            entry: dict = {"kind": "pid", "key": pid.key, "request": label}
            try:
                payload = await session.query(
                    pid.mode, pid.pid, pid_bytes=pid.pid_bytes, tx_header=pid.tx_header
                )
                entry["payload"] = payload.hex()
                try:
                    value = pid.decode(payload)
                    entry["value"] = value
                    flag = "" if pid.verified else "  [VERIFY]"
                    print(f"  {pid.key:<24} {label}  raw={payload.hex():<16} "
                          f"-> {value:.3f} {pid.unit or ''}{flag}")
                except (ValueError, IndexError) as err:
                    entry["decode_error"] = str(err)
                    print(f"  {pid.key:<24} {label}  raw={payload.hex():<16} DECODE FAIL: {err}")
            except NoDataError:
                entry["error"] = "NO DATA"
                print(f"  {pid.key:<24} {label}  NO DATA (module asleep?)")
            except (ElmError, TimeoutError) as err:
                entry["error"] = str(err)
                print(f"  {pid.key:<24} {label}  ERROR: {err}")
            results.append(entry)
    finally:
        transcript = [{"cmd": c, "raw": r} for c, r in elm.transcript]
        await transport.disconnect()

    out_dir = REPO_ROOT / "transcripts"
    out_dir.mkdir(exist_ok=True)
    out_file = out_dir / f"{args.state}.jsonl"
    with out_file.open("w", encoding="utf-8") as handle:
        meta = {"kind": "meta", "state": args.state, "profile": profile.key, "ts": time.time()}
        handle.write(json.dumps(meta) + "\n")
        for item in results:
            handle.write(json.dumps(item) + "\n")
        for item in transcript:
            handle.write(json.dumps({"kind": "io", **item}) + "\n")
    print(f"\nTranscript written to {out_file}")
    return 0


async def cmd_repl(args: argparse.Namespace) -> int:
    transport = make_transport(args.address)
    elm = Elm327(transport)
    print(f"Connecting to {args.address} ...")
    await transport.connect()
    try:
        banner = await elm.initialize()
        print(f"Adapter: {banner or '(no banner)'}; type AT/OBD commands, 'quit' to exit")
        while True:
            line = (await asyncio.to_thread(input, "elm> ")).strip()
            if line.lower() in {"quit", "exit"}:
                return 0
            if not line:
                continue
            try:
                for response_line in await elm.command(line, check_errors=False):
                    print(f"  {response_line}")
            except (ElmError, TimeoutError) as err:
                print(f"  ! {err}")
    finally:
        await transport.disconnect()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("scan", help="discover nearby BLE devices, flag OBD-looking ones")

    sweep = sub.add_parser("sweep", help="query every profile PID, save a transcript")
    sweep.add_argument("--address", required=True, help="adapter MAC address")
    sweep.add_argument(
        "--profile",
        default="chevy_bolt_2019_plus",
        choices=sorted(PROFILES),
    )
    sweep.add_argument(
        "--state",
        default="unknown",
        help="label for the transcript file: asleep / on / charging",
    )

    repl = sub.add_parser("repl", help="interactive ELM327 console")
    repl.add_argument("--address", required=True)

    args = parser.parse_args()
    handler = {"scan": cmd_scan, "sweep": cmd_sweep, "repl": cmd_repl}[args.command]
    return asyncio.run(handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
