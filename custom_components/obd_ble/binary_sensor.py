"""Presence / awake / charging state."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ObdBleConfigEntry
from .coordinator import ObdBleCoordinator, VehicleData
from .entity import ObdBleEntity


@dataclass(frozen=True, kw_only=True)
class ObdBinarySensorDescription(BinarySensorEntityDescription):
    value_fn: Callable[[VehicleData], bool]


DESCRIPTIONS: tuple[ObdBinarySensorDescription, ...] = (
    ObdBinarySensorDescription(
        key="adapter_present",
        name="Adapter in range",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.present,
    ),
    ObdBinarySensorDescription(
        key="car_awake",
        name="Car awake",
        icon="mdi:car-connected",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.car_awake,
    ),
    ObdBinarySensorDescription(
        key="charging",
        name="Charging",
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
        value_fn=lambda data: data.charging,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ObdBleConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        ObdBinarySensor(coordinator, description) for description in DESCRIPTIONS
    )


class ObdBinarySensor(ObdBleEntity, BinarySensorEntity):
    entity_description: ObdBinarySensorDescription

    def __init__(
        self, coordinator: ObdBleCoordinator, description: ObdBinarySensorDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data
        if data is None:
            return None
        return self.entity_description.value_fn(data)
