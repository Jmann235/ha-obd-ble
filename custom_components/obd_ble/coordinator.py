"""Polling coordinator: connects to the adapter when it is in BLE range.

Lifecycle per update cycle: resolve the BLE device through HA's bluetooth
stack (works through ESPHome proxies), connect, init the ELM327, read the
adapter's 12 V measurement, then poll PID groups per ECU header. The first
PID of each group acts as a probe — if the module is asleep (NO DATA) the
rest of the group is skipped so an idle cycle stays short.

Previously read values are carried forward when the car is away or asleep;
freshness is exposed via ``present`` / ``car_awake`` / ``last_success``
instead of flapping every sensor to unavailable.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

from bleak.exc import BleakError
from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    CONF_CHARGING_INTERVAL,
    CONF_IDLE_INTERVAL,
    CONF_VIN,
    DEFAULT_CHARGING_INTERVAL_SECONDS,
    DEFAULT_IDLE_INTERVAL_SECONDS,
    DOMAIN,
)
from .elm.elm327 import Elm327, ElmError, NoDataError
from .elm.obd import NegativeResponseError, ObdDecodeError, ObdSession
from .elm.transport import BleSerialTransport, BleTransportError
from .vehicles.base import PidDefinition, VehicleProfile

_LOGGER = logging.getLogger(__name__)

CYCLE_TIMEOUT_SECONDS = 90


@dataclass(frozen=True)
class VehicleData:
    """Coordinator snapshot; values persist across away/asleep cycles."""

    values: dict[str, float | None] = field(default_factory=dict)
    adapter_voltage: float | None = None
    present: bool = False
    car_awake: bool = False
    charging: bool = False
    vin: str | None = None
    last_success: datetime | None = None


class ObdBleCoordinator(DataUpdateCoordinator[VehicleData]):
    """One instance per configured car."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, profile: VehicleProfile) -> None:
        self.address: str = entry.data[CONF_ADDRESS]
        self.profile = profile
        self._idle_interval = timedelta(
            seconds=entry.options.get(CONF_IDLE_INTERVAL, DEFAULT_IDLE_INTERVAL_SECONDS)
        )
        self._charging_interval = timedelta(
            seconds=entry.options.get(CONF_CHARGING_INTERVAL, DEFAULT_CHARGING_INTERVAL_SECONDS)
        )
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_{self.address}",
            update_interval=self._idle_interval,
        )
        # Snapshot of the options this instance was built from, so the entry
        # update listener can tell an options change (reload) apart from a
        # runtime data write like the VIN (no reload).
        self.applied_options: tuple[timedelta, timedelta] = (
            self._idle_interval,
            self._charging_interval,
        )
        self._vin: str | None = entry.data.get(CONF_VIN)
        self.last_transcript: list[tuple[str, str]] = []
        self._was_present = False

    # --- presence tracking -------------------------------------------------

    def start_presence_tracking(self) -> Callable[[], None]:
        """Register advertisement callbacks; returns a cleanup callable."""
        unsub_seen = bluetooth.async_register_callback(
            self.hass,
            self._adapter_seen,
            bluetooth.BluetoothCallbackMatcher(address=self.address, connectable=True),
            bluetooth.BluetoothScanningMode.ACTIVE,
        )
        unsub_gone = bluetooth.async_track_unavailable(
            self.hass, self._adapter_gone, self.address, connectable=True
        )

        @callback
        def _cleanup() -> None:
            unsub_seen()
            unsub_gone()

        return _cleanup

    @callback
    def _adapter_seen(
        self,
        _service_info: bluetooth.BluetoothServiceInfoBleak,
        _change: bluetooth.BluetoothChange,
    ) -> None:
        if self._was_present:
            return
        self._was_present = True
        _LOGGER.debug("%s back in range; polling now", self.address)
        self.hass.async_create_task(self.async_request_refresh())

    @callback
    def _adapter_gone(self, _service_info: bluetooth.BluetoothServiceInfoBleak) -> None:
        self._was_present = False
        _LOGGER.debug("%s left BLE range", self.address)
        if self.data is not None:
            self.async_set_updated_data(
                replace(self.data, present=False, car_awake=False, charging=False)
            )

    # --- polling ------------------------------------------------------------

    def _keys_to_poll(self) -> set[str]:
        """Poll enabled sensors + inputs of enabled derived sensors."""
        registry = er.async_get(self.hass)

        def is_enabled(key: str, default: bool) -> bool:
            entity_id = registry.async_get_entity_id("sensor", DOMAIN, f"{self.address}_{key}")
            if entity_id is None:
                return default
            entity_entry = registry.async_get(entity_id)
            return entity_entry is not None and entity_entry.disabled_by is None

        keys = {
            pid.key for pid in self.profile.pids if is_enabled(pid.key, pid.enabled_default)
        }
        for derived in self.profile.derived:
            if is_enabled(derived.key, derived.enabled_default):
                keys.update(derived.inputs)
        keys.update(self.profile.charging_inputs)
        return keys

    async def _async_update_data(self) -> VehicleData:
        prev = self.data or VehicleData(vin=self._vin)

        if not bluetooth.async_address_present(self.hass, self.address, connectable=True):
            self.update_interval = self._idle_interval
            return replace(prev, present=False, car_awake=False, charging=False)

        ble_device = bluetooth.async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )
        if ble_device is None:
            self.update_interval = self._idle_interval
            return replace(prev, present=False, car_awake=False, charging=False)

        async def device_factory():
            return ble_device

        transport = BleSerialTransport(device_factory, name=self.address)
        elm = Elm327(transport)
        session = ObdSession(elm)

        values: dict[str, float | None] = dict(prev.values)
        car_awake = False
        adapter_voltage: float | None = None
        poll_keys = self._keys_to_poll()

        try:
            async with asyncio.timeout(CYCLE_TIMEOUT_SECONDS):
                await transport.connect()
                await elm.initialize()
                adapter_voltage = await elm.voltage()

                for header in self.profile.headers:
                    pids = [
                        p for p in self.profile.pids_for_header(header) if p.key in poll_keys
                    ]
                    if not pids:
                        continue
                    group_awake = await self._poll_group(session, pids, values)
                    car_awake = car_awake or group_awake

                if car_awake and self._vin is None:
                    await self._read_and_store_vin(session)
        except (BleakError, BleTransportError, ElmError, TimeoutError, OSError) as err:
            raise UpdateFailed(f"poll cycle failed: {err}") from err
        finally:
            self.last_transcript = list(elm.transcript)
            await transport.disconnect()

        for derived in self.profile.derived:
            values[derived.key] = derived.compute(values)

        charging = bool(self.profile.charging_detector(values)) if car_awake else False
        self.update_interval = self._charging_interval if charging else self._idle_interval

        return VehicleData(
            values=values,
            adapter_voltage=adapter_voltage,
            present=True,
            car_awake=car_awake,
            charging=charging,
            vin=self._vin,
            last_success=dt_util.utcnow(),
        )

    async def _poll_group(
        self,
        session: ObdSession,
        pids: list[PidDefinition],
        values: dict[str, float | None],
    ) -> bool:
        """Poll one ECU header group; first PID probes for a sleeping module."""
        for index, pid in enumerate(pids):
            try:
                payload = await session.query(
                    pid.mode, pid.pid, pid_bytes=pid.pid_bytes, tx_header=pid.tx_header
                )
                values[pid.key] = pid.decode(payload)
            except NoDataError:
                if index == 0:
                    _LOGGER.debug(
                        "Module %03X not answering; skipping group", pid.tx_header
                    )
                    return False
                _LOGGER.debug("%s: NO DATA", pid.key)
            except NegativeResponseError as err:
                # Typically an unsupported PID for this model year.
                _LOGGER.debug("%s: %s", pid.key, err)
            except (ObdDecodeError, ValueError, IndexError) as err:
                _LOGGER.warning("%s: could not decode response: %s", pid.key, err)
        return True

    async def _read_and_store_vin(self, session: ObdSession) -> None:
        vin = await session.read_vin()
        if vin is None:
            return
        self._vin = vin
        entry = self.config_entry
        self.hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_VIN: vin}
        )
        device_registry = dr.async_get(self.hass)
        device = device_registry.async_get_device(identifiers={(DOMAIN, self.address)})
        if device is not None:
            device_registry.async_update_device(device.id, serial_number=vin)
        _LOGGER.info("Read VIN %s for %s", vin, self.address)
