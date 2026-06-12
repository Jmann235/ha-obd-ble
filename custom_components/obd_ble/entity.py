"""Shared entity base: one HA device per car, keyed by adapter MAC."""

from __future__ import annotations

from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ObdBleCoordinator


class ObdBleEntity(CoordinatorEntity[ObdBleCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: ObdBleCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._key = key
        self._attr_unique_id = f"{coordinator.address}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.address)},
            connections={(CONNECTION_BLUETOOTH, coordinator.address)},
            name=coordinator.config_entry.title,
            manufacturer=coordinator.profile.manufacturer,
            model=coordinator.profile.model,
            serial_number=coordinator.data.vin if coordinator.data else None,
        )

    @property
    def available(self) -> bool:
        # Values are deliberately held while the car is away/asleep;
        # freshness is exposed through the presence/awake/last-poll entities.
        return True
