from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.components.binary_sensor import (
    DOMAIN as BINARY_SENSOR_DOMAIN,
)
from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)

from . import helpers
from .entity import MeshtasticNodeEntity

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .coordinator import MeshtasticDataUpdateCoordinator
    from .data import MeshtasticConfigEntry


def _build_binary_sensors(nodes, coordinator) -> Iterable[MeshtasticBinarySensor]:
    entities = []
    entities += [
        MeshtasticBinarySensor(
            coordinator=coordinator,
            entity_description=MeshtasticBinarySensorEntityDescription(
                key="device_powered",
                name="Powered",
                icon="mdi:power-plug",
                device_class=BinarySensorDeviceClass.POWER,
                value_fn=lambda device: device.coordinator.data[device.node_id]
                .get("deviceMetrics", {})
                .get("batteryLevel", 0)
                > 100,
            ),
            node_id=node_id,
        )
        for node_id, node_info in nodes.items()
    ]

    return entities


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MeshtasticConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the binary_sensor platform."""
    await helpers.setup_platform_entry(
        hass, entry, async_add_entities, _build_binary_sensors
    )


async def async_unload_entry(
    hass: HomeAssistant,
    entry: MeshtasticConfigEntry,
) -> bool:
    return await helpers.async_unload_entry(hass, entry)


@dataclass(kw_only=True)
class MeshtasticBinarySensorEntityDescription(BinarySensorEntityDescription):
    value_fn: Callable[[MeshtasticBinarySensor], bool]


class MeshtasticBinarySensor(MeshtasticNodeEntity, BinarySensorEntity):
    entity_description: MeshtasticBinarySensorEntityDescription

    def __init__(
        self,
        coordinator: MeshtasticDataUpdateCoordinator,
        entity_description: MeshtasticBinarySensorEntityDescription,
        node_id: int,
    ) -> None:
        """Initialize the binary_sensor class."""
        super().__init__(coordinator, node_id, BINARY_SENSOR_DOMAIN, entity_description)

    def _async_update_attrs(self) -> None:
        self._attr_is_on = self.entity_description.value_fn(self)
