from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_platform
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LOGGER, MeshtasticDataUpdateCoordinator, MeshtasticEntity
from .const import CONF_OPTION_FILTER_NODES
from .data import MeshtasticConfigEntry


def get_nodes(entry: MeshtasticConfigEntry):
    filter_nodes = entry.options.get(CONF_OPTION_FILTER_NODES, [])
    filter_node_nums = [el["id"] for el in filter_nodes]
    nodes = {
        node_num: node_info
        for node_num, node_info in entry.runtime_data.coordinator.data.items()
        if node_num in filter_node_nums
    }
    return nodes


_remove_listeners = defaultdict(lambda: defaultdict(list))


async def setup_platform_entry(
    hass: HomeAssistant,  # noqa: ARG001 Unused function argument: `hass`
    entry: MeshtasticConfigEntry,
    async_add_entities: AddEntitiesCallback,
    entity_factory: Callable[
        [dict[str, Any], MeshtasticDataUpdateCoordinator], Iterable[MeshtasticEntity]
    ],
) -> None:
    async_add_entities(entity_factory(get_nodes(entry), entry.runtime_data.coordinator))
    platform = entity_platform.async_get_current_platform()

    def on_coordinator_data_update():
        entities = entity_factory(get_nodes(entry), entry.runtime_data.coordinator)
        new_entities = [s for s in entities if s.entity_id not in platform.entities]
        if new_entities:
            async_add_entities(new_entities)

    remove_listener = entry.runtime_data.coordinator.async_add_listener(
        on_coordinator_data_update
    )
    _remove_listeners[platform.domain][entry.entry_id].append(remove_listener)


async def async_unload_entry(
    hass: HomeAssistant,
    entry: MeshtasticConfigEntry,
) -> bool:
    platform = entity_platform.async_get_current_platform()
    for remove_listener in _remove_listeners[platform.domain].pop(entry.entry_id, []):
        remove_listener()

    return True


async def fetch_meshtastic_hardware_names(hass):
    try:
        session = async_get_clientsession(hass)
        async with session.get(
            "https://api.meshtastic.org/resource/deviceHardware", raise_for_status=True
        ) as response:
            response_json = await response.json()
            device_hardware_names = {
                h["hwModelSlug"]: h["displayName"] for h in response_json
            }
    except Exception:
        LOGGER.info("Failed to fetch meshtastic hardware infos", exc_info=True)
        device_hardware_names = {}
    return device_hardware_names
