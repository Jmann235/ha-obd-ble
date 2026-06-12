"""OBD BLE Vehicle Monitor: poll a car over a Bluetooth LE OBD-II adapter."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import (
    CONF_CHARGING_INTERVAL,
    CONF_IDLE_INTERVAL,
    CONF_PROFILE,
    DEFAULT_CHARGING_INTERVAL_SECONDS,
    DEFAULT_IDLE_INTERVAL_SECONDS,
    DOMAIN,
    FRONTEND_CARD_FILENAME,
    FRONTEND_URL_BASE,
    FRONTEND_VERSION,
)
from .coordinator import ObdBleCoordinator
from .vehicles.registry import get_profile

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]

type ObdBleConfigEntry = ConfigEntry[ObdBleCoordinator]

_FRONTEND_REGISTERED = "frontend_registered"


async def _async_register_frontend(hass: HomeAssistant) -> None:
    """Serve the bundled dashboard card and load it on every dashboard.

    add_extra_js_url means installing the integration installs the card —
    no manual Lovelace resource management.
    """
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get(_FRONTEND_REGISTERED):
        return
    domain_data[_FRONTEND_REGISTERED] = True
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                FRONTEND_URL_BASE,
                str(Path(__file__).parent / "frontend"),
                cache_headers=True,
            )
        ]
    )
    add_extra_js_url(
        hass, f"{FRONTEND_URL_BASE}/{FRONTEND_CARD_FILENAME}?v={FRONTEND_VERSION}"
    )


async def async_setup_entry(hass: HomeAssistant, entry: ObdBleConfigEntry) -> bool:
    await _async_register_frontend(hass)
    profile = get_profile(entry.data[CONF_PROFILE])
    coordinator = ObdBleCoordinator(hass, entry, profile)
    entry.async_on_unload(coordinator.start_presence_tracking())

    # First refresh returns quickly with an empty snapshot when the car is
    # away — setup must not depend on the vehicle being home.
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_options_updated(hass: HomeAssistant, entry: ObdBleConfigEntry) -> None:
    """Reload only for real options changes.

    The coordinator also writes to entry.data at runtime (first VIN read),
    which fires this listener too — that must not bounce the integration.
    """
    coordinator = entry.runtime_data
    requested = (
        timedelta(seconds=entry.options.get(CONF_IDLE_INTERVAL, DEFAULT_IDLE_INTERVAL_SECONDS)),
        timedelta(
            seconds=entry.options.get(
                CONF_CHARGING_INTERVAL, DEFAULT_CHARGING_INTERVAL_SECONDS
            )
        ),
    )
    if requested == coordinator.applied_options:
        return
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ObdBleConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
