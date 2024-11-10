from __future__ import annotations

from abc import ABC, abstractmethod

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import MeshtasticDataUpdateCoordinator


class MeshtasticEntity(CoordinatorEntity[MeshtasticDataUpdateCoordinator]):
    def __init__(self, coordinator: MeshtasticDataUpdateCoordinator) -> None:
        super().__init__(coordinator)

    @callback
    def _handle_coordinator_update(self) -> None:
        self._async_update_attrs()
        super()._handle_coordinator_update()

    def update(self):
        self._async_update_attrs()
        self.async_write_ha_state()

    @abstractmethod
    def _async_update_attrs(self) -> None:
        pass


class MeshtasticNodeEntity(MeshtasticEntity, ABC):
    def __init__(
        self,
        coordinator: MeshtasticDataUpdateCoordinator,
        node_id: int,
        platform: str,
        entity_description: EntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self._node_id = node_id

        self.entity_description = entity_description
        self.entity_id = (
            f"{platform}.{DOMAIN}_{self.node_id}_{self.entity_description.key}"
        )

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self.node_id)},
        )
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_{platform}_{self.node_id}_{self.entity_description.key}"

        self._async_update_attrs()

    @property
    def node_id(self) -> int:
        return self._node_id

    @property
    def available(self) -> bool:
        return super().available and self.node_id in self.coordinator.data
