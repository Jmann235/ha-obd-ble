"""Config flow: BLE discovery or manual MAC, vehicle profile, poll options."""

from __future__ import annotations

import re
from typing import Any

import voluptuous as vol
from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_ADDRESS, CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    CONF_CHARGING_INTERVAL,
    CONF_IDLE_INTERVAL,
    CONF_PROFILE,
    DEFAULT_CHARGING_INTERVAL_SECONDS,
    DEFAULT_IDLE_INTERVAL_SECONDS,
    DOMAIN,
    MIN_INTERVAL_SECONDS,
)
from .elm.transport import ADVERTISED_SERVICE_UUIDS
from .vehicles.registry import PROFILES

_MAC_RE = re.compile(r"^(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$")

_OBD_NAME_HINTS = ("veepeak", "obd", "vlink", "elm")

DEFAULT_PROFILE = "chevy_bolt_2019_plus"


def _profile_selector() -> SelectSelector:
    return SelectSelector(
        SelectSelectorConfig(
            options=[
                SelectOptionDict(value=profile.key, label=profile.name)
                for profile in PROFILES.values()
            ],
            mode=SelectSelectorMode.DROPDOWN,
        )
    )


def _looks_like_obd(service_info: BluetoothServiceInfoBleak) -> bool:
    known = {uuid.lower() for uuid in ADVERTISED_SERVICE_UUIDS}
    if {uuid.lower() for uuid in service_info.service_uuids} & known:
        return True
    name = (service_info.name or "").lower()
    return any(hint in name for hint in _OBD_NAME_HINTS)


def _device_label(service_info: BluetoothServiceInfoBleak) -> str:
    name = service_info.name or "Unknown"
    label = f"{name} ({service_info.address}) RSSI {service_info.rssi} dBm"
    if _looks_like_obd(service_info):
        label += " — likely OBD adapter"
    if not service_info.connectable:
        label += " [not connectable]"
    return label


class ObdBleConfigFlow(ConfigFlow, domain=DOMAIN):
    """Per-car setup; unique id is the adapter MAC."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovery_info: BluetoothServiceInfoBleak | None = None

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """A matching adapter started advertising."""
        await self.async_set_unique_id(format_mac(discovery_info.address))
        self._abort_if_unique_id_configured()
        self._discovery_info = discovery_info
        self.context["title_placeholders"] = {
            "name": discovery_info.name or discovery_info.address
        }
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the vehicle profile for a discovered adapter."""
        assert self._discovery_info is not None
        if user_input is not None:
            return self.async_create_entry(
                title=user_input[CONF_NAME],
                data={
                    CONF_ADDRESS: self._discovery_info.address,
                    CONF_PROFILE: user_input[CONF_PROFILE],
                },
            )

        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_NAME, default="Chevy Bolt"): str,
                    vol.Required(CONF_PROFILE, default=DEFAULT_PROFILE): _profile_selector(),
                }
            ),
            description_placeholders={
                "name": self._discovery_info.name or self._discovery_info.address,
                "address": self._discovery_info.address,
            },
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manual setup: choose a discovered adapter or type a MAC."""
        errors: dict[str, str] = {}
        if user_input is not None:
            address = user_input[CONF_ADDRESS].strip()
            if not _MAC_RE.match(address):
                errors[CONF_ADDRESS] = "invalid_address"
            else:
                await self.async_set_unique_id(format_mac(address), raise_on_progress=False)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=user_input[CONF_NAME],
                    data={
                        CONF_ADDRESS: address.upper().replace("-", ":"),
                        CONF_PROFILE: user_input[CONF_PROFILE],
                    },
                )

        # Unfiltered on purpose: listing every advertisement HA's Bluetooth stack can
        # see (including non-connectable proxy sightings) makes this step double as a
        # diagnostic — an empty list means HA has no working radio/proxy, not that the
        # adapter merely failed to match a filter.
        current_addresses = self._async_current_ids(include_ignore=False)
        all_advertisements = bluetooth.async_discovered_service_info(
            self.hass, connectable=False
        )
        discovered = [
            service_info
            for service_info in all_advertisements
            if format_mac(service_info.address) not in current_addresses
        ]
        discovered.sort(key=lambda info: (not _looks_like_obd(info), -info.rssi))
        options = [
            SelectOptionDict(value=service_info.address, label=_device_label(service_info))
            for service_info in discovered
        ]

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ADDRESS): SelectSelector(
                        SelectSelectorConfig(
                            options=options,
                            custom_value=True,
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Required(CONF_NAME, default="Chevy Bolt"): str,
                    vol.Required(CONF_PROFILE, default=DEFAULT_PROFILE): _profile_selector(),
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> ObdBleOptionsFlow:
        return ObdBleOptionsFlow()


class ObdBleOptionsFlow(OptionsFlow):
    """Poll interval tuning."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        options = self.config_entry.options
        interval_selector = NumberSelector(
            NumberSelectorConfig(
                min=MIN_INTERVAL_SECONDS,
                max=3600,
                step=10,
                unit_of_measurement="s",
                mode=NumberSelectorMode.BOX,
            )
        )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_IDLE_INTERVAL,
                        default=options.get(CONF_IDLE_INTERVAL, DEFAULT_IDLE_INTERVAL_SECONDS),
                    ): interval_selector,
                    vol.Required(
                        CONF_CHARGING_INTERVAL,
                        default=options.get(
                            CONF_CHARGING_INTERVAL, DEFAULT_CHARGING_INTERVAL_SECONDS
                        ),
                    ): interval_selector,
                }
            ),
        )
