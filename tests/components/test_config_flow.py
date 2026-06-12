"""Config flow behavior under the HA test harness."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.const import CONF_ADDRESS, CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.obd_ble.const import CONF_PROFILE, DOMAIN

ADDRESS = "AA:BB:CC:DD:EE:FF"


@pytest.fixture(autouse=True)
def no_discovery():
    """The user step lists discovered adapters; keep it empty and offline."""
    with patch(
        "custom_components.obd_ble.config_flow.bluetooth.async_discovered_service_info",
        return_value=[],
    ):
        yield


@pytest.fixture(autouse=True)
def no_setup():
    """Entry creation must not start the real coordinator/BLE stack."""
    with patch("custom_components.obd_ble.async_setup_entry", return_value=True):
        yield


def _fake_service_info(
    address: str,
    name: str | None,
    rssi: int = -60,
    service_uuids: list[str] | None = None,
    connectable: bool = True,
) -> SimpleNamespace:
    return SimpleNamespace(
        address=address,
        name=name,
        rssi=rssi,
        service_uuids=service_uuids or [],
        connectable=connectable,
    )


def _address_options(result) -> list[dict]:
    schema = result["data_schema"].schema
    selector = next(value for key, value in schema.items() if str(key) == CONF_ADDRESS)
    return list(selector.config["options"])


async def test_user_flow_lists_all_devices_obd_first(hass: HomeAssistant) -> None:
    """The picker is unfiltered (diagnostic) but sorts likely OBD adapters first."""
    phone = _fake_service_info("11:11:11:11:11:11", "Pixel 9", rssi=-40)
    adapter = _fake_service_info("22:22:22:22:22:22", "VEEPEAK", rssi=-75)
    unnamed = _fake_service_info("33:33:33:33:33:33", None, rssi=-90, connectable=False)

    with patch(
        "custom_components.obd_ble.config_flow.bluetooth.async_discovered_service_info",
        return_value=[phone, adapter, unnamed],
    ):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})

    options = _address_options(result)
    assert [option["value"] for option in options] == [
        "22:22:22:22:22:22",  # OBD-looking first despite weakest-but-one RSSI
        "11:11:11:11:11:11",  # then everything else by signal strength
        "33:33:33:33:33:33",
    ]
    assert "likely OBD adapter" in options[0]["label"]
    assert "RSSI -40 dBm" in options[1]["label"]
    assert "[not connectable]" in options[2]["label"]


async def test_user_flow_manual_address(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            CONF_ADDRESS: ADDRESS,
            CONF_NAME: "Bolt",
            CONF_PROFILE: "chevy_bolt_2019_plus",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Bolt"
    assert result["data"][CONF_ADDRESS] == ADDRESS
    assert result["data"][CONF_PROFILE] == "chevy_bolt_2019_plus"


async def test_user_flow_rejects_bad_mac(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            CONF_ADDRESS: "not-a-mac",
            CONF_NAME: "Bolt",
            CONF_PROFILE: "chevy_bolt_2019_plus",
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_ADDRESS: "invalid_address"}


async def test_duplicate_address_aborts(hass: HomeAssistant) -> None:
    MockConfigEntry(
        domain=DOMAIN,
        unique_id=ADDRESS.lower(),
        data={CONF_ADDRESS: ADDRESS, CONF_PROFILE: "chevy_bolt_2019_plus"},
    ).add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            CONF_ADDRESS: ADDRESS,
            CONF_NAME: "Bolt",
            CONF_PROFILE: "chevy_bolt_2019_plus",
        },
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
