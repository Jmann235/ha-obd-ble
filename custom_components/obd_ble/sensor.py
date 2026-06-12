"""Sensors generated from the vehicle profile's PID table."""

from __future__ import annotations

import logging
from datetime import datetime

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ObdBleConfigEntry
from .coordinator import ObdBleCoordinator
from .entity import ObdBleEntity

_LOGGER = logging.getLogger(__name__)


def _device_class(value: str | None) -> SensorDeviceClass | None:
    if value is None:
        return None
    try:
        return SensorDeviceClass(value)
    except ValueError:
        _LOGGER.warning("Unknown sensor device class %r", value)
        return None


def _state_class(value: str | None) -> SensorStateClass | None:
    if value is None:
        return None
    try:
        return SensorStateClass(value)
    except ValueError:
        _LOGGER.warning("Unknown sensor state class %r", value)
        return None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ObdBleConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entities: list[ObdBleEntity] = []

    for item in (*coordinator.profile.pids, *coordinator.profile.derived):
        description = SensorEntityDescription(
            key=item.key,
            name=item.name,
            native_unit_of_measurement=item.unit,
            device_class=_device_class(item.device_class),
            state_class=_state_class(item.state_class),
            icon=item.icon,
            entity_registry_enabled_default=item.enabled_default,
            suggested_display_precision=item.precision,
        )
        entities.append(ObdValueSensor(coordinator, description))

    entities.append(
        ObdAdapterVoltageSensor(
            coordinator,
            SensorEntityDescription(
                key="adapter_voltage",
                name="12V battery voltage",
                native_unit_of_measurement="V",
                device_class=SensorDeviceClass.VOLTAGE,
                state_class=SensorStateClass.MEASUREMENT,
                suggested_display_precision=1,
            ),
        )
    )
    entities.append(
        ObdLastPollSensor(
            coordinator,
            SensorEntityDescription(
                key="last_poll",
                name="Last successful poll",
                device_class=SensorDeviceClass.TIMESTAMP,
                entity_category=EntityCategory.DIAGNOSTIC,
            ),
        )
    )

    async_add_entities(entities)


class ObdValueSensor(ObdBleEntity, RestoreSensor):
    """A decoded PID or derived value; holds last reading across restarts."""

    def __init__(
        self, coordinator: ObdBleCoordinator, description: SensorEntityDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description
        self._restored_value: float | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self.coordinator.data and self.coordinator.data.values.get(self._key) is not None:
            return
        if (stored := await self.async_get_last_sensor_data()) is not None and isinstance(
            stored.native_value, (int, float)
        ):
            self._restored_value = float(stored.native_value)

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data
        if data is not None and (value := data.values.get(self._key)) is not None:
            return value
        return self._restored_value


class ObdAdapterVoltageSensor(ObdBleEntity, RestoreSensor):
    """The adapter's own measurement of the car's 12 V rail (ATRV)."""

    def __init__(
        self, coordinator: ObdBleCoordinator, description: SensorEntityDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description
        self._restored_value: float | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (stored := await self.async_get_last_sensor_data()) is not None and isinstance(
            stored.native_value, (int, float)
        ):
            self._restored_value = float(stored.native_value)

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data
        if data is not None and data.adapter_voltage is not None:
            return data.adapter_voltage
        return self._restored_value


class ObdLastPollSensor(ObdBleEntity, RestoreSensor):
    """Timestamp of the last cycle that actually reached the adapter."""

    def __init__(
        self, coordinator: ObdBleCoordinator, description: SensorEntityDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description
        self._restored_value: datetime | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (stored := await self.async_get_last_sensor_data()) is not None and isinstance(
            stored.native_value, datetime
        ):
            self._restored_value = stored.native_value

    @property
    def native_value(self) -> datetime | None:
        data = self.coordinator.data
        if data is not None and data.last_success is not None:
            return data.last_success
        return self._restored_value
