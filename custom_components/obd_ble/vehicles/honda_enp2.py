"""Honda e:NP2: dashboard-verified SOC and odometer over 29-bit CAN.

Verified on an e:NP2 on 2026-11-01 using an iCar Pro 2S through an ESPHome
proxy. DID 202A returned 243 data bytes; B50 matched the dashboard's 81%.
DID 7022 returned 54 data bytes; B5..B8 were 00 00 23 67 = 9063 km,
independently confirmed on the dashboard. Offsets exclude the 62/DID echo.

Only these verified fields are exposed. Battery health, current, voltage,
temperatures and charging state are not inferred from other Honda models.
Adapter-measured 12V voltage remains available through the common ATRV sensor.
"""

from .base import PidDefinition, VehicleProfile, u8, u32

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
    ),
)
