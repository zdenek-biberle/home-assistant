from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING

from homeassistant.components.notify import NotifyEntity, NotifyEntityFeature
from homeassistant.helpers import entity_platform
from homeassistant.helpers import entity_registry as er

from . import DOMAIN, SERVICE_SEND_TEXT
from .api import (
    ATTR_EVENT_MESHTASTIC_API_CONFIG_ENTRY_ID,
    ATTR_EVENT_MESHTASTIC_API_DATA,
    ATTR_EVENT_MESHTASTIC_API_NODE,
    EVENT_MESHTASTIC_API_NODE_UPDATED,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import Event, HomeAssistant, _DataT
    from homeassistant.helpers.device_registry import DeviceInfo
    from homeassistant.helpers.entity_platform import AddEntitiesCallback


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    nodes = await config_entry.runtime_data.client.async_get_all_nodes()
    entities = [
        MeshtasticNodeNotify(
            node_id=node_id,
            entity_name=f"{node_info["user"]["longName"]}",
        )
        for node_id, node_info in nodes.items()
    ]

    platform = entity_platform.async_get_current_platform()
    entity_registry = er.async_get(hass)
    new_entities = []
    for e in entities:
        registered_entity_id = entity_registry.async_get_entity_id(platform.domain, platform.platform_name, e.unique_id)
        if registered_entity_id is None or registered_entity_id not in platform.domain_entities:
            new_entities.append(e)

    async_add_entities(new_entities)

    def _api_node_updated(event: Event[_DataT]) -> None:
        event_data = deepcopy(event.data)
        config_entry_id = event_data.pop(ATTR_EVENT_MESHTASTIC_API_CONFIG_ENTRY_ID, None)
        if config_entry_id != config_entry.entry_id:
            return
        node_id = event_data.get(ATTR_EVENT_MESHTASTIC_API_NODE, None)
        node_info = event_data.get(ATTR_EVENT_MESHTASTIC_API_DATA, None)

        if "user" not in node_info or "longName" not in node_info["user"]:
            return

        entity = MeshtasticNodeNotify(node_id=node_id, entity_name=f"{node_info["user"]["longName"]}")
        registered_entity_id = entity_registry.async_get_entity_id(platform.domain, platform.platform_name, e.unique_id)
        if registered_entity_id is None or registered_entity_id not in platform.domain_entities:
            async_add_entities([entity])

    hass.bus.async_listen(EVENT_MESHTASTIC_API_NODE_UPDATED, _api_node_updated)


async def async_unload_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> bool:
    pass


class MeshtasticNodeNotify(NotifyEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        node_id: int,
        entity_name: str | None,
        device_info: DeviceInfo | None = None,
        supported_features: NotifyEntityFeature = None,
    ) -> None:
        self._node_id = node_id
        self._attr_unique_id = f"meshtastic_{node_id}"
        self._attr_supported_features = supported_features if supported_features is not None else NotifyEntityFeature(0)
        self._attr_device_info = device_info
        self._attr_name = entity_name

    async def async_send_message(self, message: str, title: str | None = None) -> None:  # noqa: ARG002
        service_data = {"text": message, "to": str(self._node_id), "ack": True}
        await self.hass.services.async_call(DOMAIN, SERVICE_SEND_TEXT, service_data, blocking=True)

    @property
    def suggested_object_id(self) -> str | None:
        return f"mesh {self._attr_name}"
