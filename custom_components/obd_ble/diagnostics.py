"""Diagnostics dump: decoded snapshot + raw ELM transcript.

The transcript is the tool for verifying PID formulas and adding new
vehicles — download diagnostics while the car is awake and every raw
response line is in there.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant

from . import ObdBleConfigEntry
from .const import CONF_VIN

REDACT = {CONF_ADDRESS, CONF_VIN}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ObdBleConfigEntry
) -> dict[str, Any]:
    coordinator = entry.runtime_data
    data = coordinator.data
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), REDACT),
            "options": dict(entry.options),
        },
        "profile": coordinator.profile.key,
        "snapshot": None
        if data is None
        else {
            "values": data.values,
            "adapter_voltage": data.adapter_voltage,
            "present": data.present,
            "car_awake": data.car_awake,
            "charging": data.charging,
            "last_success": data.last_success.isoformat() if data.last_success else None,
        },
        "transcript": [
            {"cmd": cmd, "response": response}
            for cmd, response in coordinator.last_transcript
        ],
    }
