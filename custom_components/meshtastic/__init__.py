"""
Custom integration to integrate Meshtastic with Home Assistant.

For more details about this integration, please refer to
https://github.com/broglep/homeassistant-meshtastic
"""

from __future__ import annotations

import base64
import dataclasses
import datetime
from collections import defaultdict
from collections.abc import Awaitable, Callable, MutableMapping
from copy import deepcopy
from enum import StrEnum

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import (
    CONF_DEVICE_ID,
    CONF_HOST,
    CONF_PORT,
    CONF_TYPE,
    Platform,
)
from homeassistant.core import (
    Event,
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_component import EntityComponent
from homeassistant.helpers.typing import UNDEFINED, ConfigType
from homeassistant.loader import async_get_loaded_integration
from homeassistant.util.hass_dict import HassKey

from meshtastic import BROADCAST_ADDR

from .api import (
    ATTR_EVENT_MESHTASTIC_API_CONFIG_ENTRY_ID,
    ATTR_EVENT_MESHTASTIC_API_DATA,
    EVENT_MESHTASTIC_API_TEXT_MESSAGE,
    MeshtasticApiClient,
)
from .const import CONF_OPTION_FILTER_NODES, DOMAIN, LOGGER, SERVICE_SEND_TEXT
from .coordinator import MeshtasticDataUpdateCoordinator
from .data import MeshtasticConfigEntry, MeshtasticData
from .device_trigger import TRIGGER_MESSAGE_RECEIVED, TRIGGER_MESSAGE_SENT
from .entity import MeshtasticEntity
from .helpers import fetch_meshtastic_hardware_names

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
]

ENTITY_ID_FORMAT = DOMAIN + ".{}"
PLATFORM_SCHEMA = cv.PLATFORM_SCHEMA
PLATFORM_SCHEMA_BASE = cv.PLATFORM_SCHEMA_BASE
SCAN_INTERVAL = datetime.timedelta(hours=1)

DATA_COMPONENT: HassKey[EntityComponent[MeshtasticEntity]] = HassKey(DOMAIN)

SERVICE_SEND_TEXT_SCHEMA = vol.Schema(
    {
        vol.Required("text"): cv.string,
        vol.Required("to"): cv.string,
        vol.Optional("from"): cv.string,
        vol.Required("ack", default=False): cv.boolean,
    }
)

_SEND_TEXT_CANT_HANDLE_RESPONSE = object()
_service_send_text_handlers: dict[
    str, Callable[[ServiceCall], Awaitable[ServiceResponse]]
] = {}
_remove_listeners = defaultdict(list)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    component = hass.data[DATA_COMPONENT] = EntityComponent[MeshtasticEntity](
        LOGGER, DOMAIN, hass, SCAN_INTERVAL
    )

    await component.async_setup(config)

    # handler that forwards service call to appropriate handler from config entry
    async def handle_service_send_text(call: ServiceCall) -> ServiceResponse:
        for _handle_send_text_handler in _service_send_text_handlers.values():
            res = await _handle_send_text_handler(call)
            if res != _SEND_TEXT_CANT_HANDLE_RESPONSE:
                return res

    hass.services.async_register(
        DOMAIN,
        SERVICE_SEND_TEXT,
        handle_service_send_text,
        schema=SERVICE_SEND_TEXT_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )

    return True


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MeshtasticConfigEntry,
) -> bool:
    coordinator = MeshtasticDataUpdateCoordinator(hass=hass)
    if coordinator.config_entry is None:
        coordinator.config_entry = entry

    client = MeshtasticApiClient(
        hostname=f"{entry.data[CONF_HOST]}:{entry.data[CONF_PORT]}",
        hass=hass,
        config_entry_id=entry.entry_id,
    )

    await client.connect()
    gateway_node = await client.async_get_own_node()
    gateway_node_id = gateway_node["num"]
    nodes = await client.async_get_all_nodes()
    device_registry = dr.async_get(hass)

    filter_nodes = entry.options.get(CONF_OPTION_FILTER_NODES, [])
    filter_node_nums = [el["id"] for el in filter_nodes]

    device_hardware_names = await fetch_meshtastic_hardware_names(hass)

    for node_id, node in nodes.items():
        if node_id in filter_node_nums:
            mac_address = (
                base64.b64decode(node["user"]["macaddr"]).hex(":")
                if "macaddr" in node["user"]
                else None
            )

            connections = set()
            if mac_address:
                connections.add((dr.CONNECTION_NETWORK_MAC, mac_address))
            hops_away = node.get("hopsAway", 99)
            snr = node.get("snr", 0)

            existing_device = device_registry.async_get_device(
                identifiers={(DOMAIN, node_id)}
            )
            if existing_device is not None and existing_device.config_entries != {
                entry.entry_id
            }:
                # get other meshtastic connections
                meshtastic_connections = [
                    tuple(v.split("/"))
                    for k, v in existing_device.connections
                    if k == DOMAIN and not v.startswith(f"{gateway_node_id}/")
                ]
                if node_id == gateway_node_id:
                    # add ourselves with highest prio so we don't get another via device
                    meshtastic_connections.append((gateway_node_id, node_id, -1, 999))
                else:
                    meshtastic_connections.append(
                        (gateway_node_id, node_id, hops_away, snr)
                    )
                try:
                    sorted_connections = sorted(
                        meshtastic_connections, key=lambda x: (int(x[2]), -float(x[3]))
                    )
                    closest_gateway = int(sorted_connections[0][0])
                    via_device = (DOMAIN, closest_gateway)
                except Exception:
                    LOGGER.warning("Failed to find closest gateway", exc_info=True)
            else:
                via_device = (
                    (DOMAIN, gateway_node["num"])
                    if gateway_node["num"] != node_id
                    else None
                )

                # existing_device.config_entries.s = {entry.entry_id}

            # remove via_device when it is set to ourself
            if (via_device is not None and via_device[1] == node_id) or (
                gateway_node_id == node_id
            ):
                via_device = None

            if existing_device:
                connections.update(existing_device.connections)

            # remove our own entry
            connections = set(
                (k, v)
                for k, v in connections
                if k != DOMAIN
                or (k == DOMAIN and not v.startswith(f"{gateway_node_id}/"))
            )
            # add our own entry with updated data
            if gateway_node_id != node_id:
                connections.add(
                    (DOMAIN, f"{gateway_node_id}/{node_id}/{hops_away}/{snr}")
                )

            d = device_registry.async_get_or_create(
                config_entry_id=entry.entry_id,
                identifiers={(DOMAIN, node_id)},
                name=node["user"]["longName"],
                model=device_hardware_names.get(node["user"]["hwModel"], None),
                model_id=node["user"]["hwModel"],
                serial_number=node["user"]["id"],
                via_device=via_device,
                sw_version=client._interface.metadata.firmware_version
                if gateway_node["num"] == node_id and client._interface.metadata
                else None,
            )

            device_registry.async_update_device(
                d.id,
                new_connections=connections,
                via_device_id=None if via_device is None else UNDEFINED,
            )

        else:
            device = device_registry.async_get_device(identifiers={(DOMAIN, node_id)})
            # only clean up devices if they are exclusively from us
            if device:
                if device.config_entries == {entry.entry_id}:
                    device_registry.async_remove_device(device.id)
                else:
                    device_registry.async_update_device(
                        device.id, remove_config_entry_id=entry.entry_id
                    )

    local_config = await client.async_get_node_local_config()
    module_config = await client.async_get_node_module_config()

    gateway_node_entity = GatewayEntity(
        config_entry_id=entry.entry_id,
        node=gateway_node["num"],
        long_name=gateway_node["user"]["longName"],
        short_name=gateway_node["user"]["shortName"],
        local_config=local_config,
        module_config=module_config,
    )

    await _add_entities_for_entry(hass, [gateway_node_entity], entry)

    channels = await client.async_get_channels()
    channel_entities = [
        GatewayChannelEntity(
            config_entry_id=entry.entry_id,
            gateway_node=gateway_node["num"],
            gateway_entity=gateway_node_entity,
            index=channel["index"],
            name=channel["settings"]["name"],
            primary=channel["role"] == "PRIMARY",
            secondary=channel["role"] == "SECONDARY",
            settings=channel["settings"],
        )
        for channel in channels
        if channel["role"] != "DISABLED"
    ]

    await _add_entities_for_entry(hass, channel_entities, entry)

    entry.runtime_data = MeshtasticData(
        client=client,
        integration=async_get_loaded_integration(hass, entry.domain),
        coordinator=coordinator,
        gateway_node=gateway_node,
    )

    await coordinator.async_config_entry_first_refresh()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    async def handle_send_text(call: ServiceCall) -> ServiceResponse:

        def _convert_device_id_to_node_id(device_id):
            device = device_registry.async_get(device_id)
            if device is None:
                raise ServiceValidationError(f"No device found with id {from_id}")

            return next((i[1] for i in device.identifiers if i[0] == DOMAIN), None)

        if "from" in call.data:
            from_id = call.data["from"]
            if from_id.startswith("!"):
                if gateway_node["user"]["id"] != from_id:
                    return _SEND_TEXT_CANT_HANDLE_RESPONSE
            elif from_id.isnumeric():
                if int(from_id) != gateway_node["num"]:
                    return _SEND_TEXT_CANT_HANDLE_RESPONSE
            elif from_id.isalnum():
                node_id = _convert_device_id_to_node_id(from_id)
                if node_id != gateway_node["num"]:
                    return _SEND_TEXT_CANT_HANDLE_RESPONSE
        try:
            if "to" in call.data:
                to = call.data["to"]
                if to.startswith("!"):
                    pass
                elif to.isnumeric():
                    to = int(to)
                elif to.isalnum():
                    to = _convert_device_id_to_node_id(to)
            else:
                to = BROADCAST_ADDR

            await client.send_text(
                text=call.data["text"], destination_id=to, want_ack=call.data["ack"]
            )

            if not call.return_response:
                return None
            return {"to": to}
        except Exception as e:
            LOGGER.exception("Error sending text")
            raise ServiceValidationError("Failed to send text") from e

    _service_send_text_handlers[entry.entry_id] = handle_send_text

    async def _on_text_message(event: Event) -> None:
        event_data = deepcopy(event.data)
        config_entry_id = event_data.pop(
            ATTR_EVENT_MESHTASTIC_API_CONFIG_ENTRY_ID, None
        )
        if config_entry_id != entry.entry_id:
            return

        data = event_data.get(ATTR_EVENT_MESHTASTIC_API_DATA, None)
        if data is None:
            return

        from_node_id = data["from"]
        from_device = device_registry.async_get_device(
            identifiers={(DOMAIN, from_node_id)}
        )
        to_node_id = data["to"]
        to_device = device_registry.async_get_device(identifiers={(DOMAIN, to_node_id)})

        if from_device:
            hass.bus.async_fire(
                event_type=f"{DOMAIN}_event",
                event_data={
                    CONF_DEVICE_ID: from_device.id,
                    CONF_TYPE: TRIGGER_MESSAGE_SENT,
                    "message": data["message"],
                },
            )

        if to_device:
            hass.bus.async_fire(
                event_type=f"{DOMAIN}_event",
                event_data={
                    CONF_DEVICE_ID: to_device.id,
                    CONF_TYPE: TRIGGER_MESSAGE_RECEIVED,
                    "message": data["message"],
                },
            )

    _remove_listeners[entry.entry_id].append(
        hass.bus.async_listen(EVENT_MESHTASTIC_API_TEXT_MESSAGE, _on_text_message)
    )

    return True


async def async_unload_entry(
    hass: HomeAssistant,
    entry: MeshtasticConfigEntry,
) -> bool:
    for entity in [
        e
        for e in hass.data[DATA_COMPONENT].entities
        if e.registry_entry.config_entry_id == entry.entry_id
    ]:
        await hass.data[DATA_COMPONENT].async_remove_entity(entity.entity_id)

    if entry.runtime_data and entry.runtime_data.client:
        entry.runtime_data.client.close()

    del _service_send_text_handlers[entry.entry_id]

    for remove_listener in _remove_listeners.pop(entry.entry_id, []):
        remove_listener()

    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_reload_entry(
    hass: HomeAssistant,
    entry: MeshtasticConfigEntry,
) -> None:
    if config_entries.current_entry.get() is None:
        config_entries.current_entry.set(entry)
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)


async def _add_entities_for_entry(
    hass: HomeAssistant, entities: list[Entity], entry: MeshtasticConfigEntry
):
    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)

    await hass.data[DATA_COMPONENT].async_add_entities(entities)
    # attach entities to config entry (as async_add_entities does not support apply config_entry_id from entities)
    for e in entities:
        device_id = UNDEFINED
        if e.device_info:
            device = device_registry.async_get_device(
                identifiers=e.device_info["identifiers"]
            )
            if device:
                device_id = device.id
        try:
            entity_registry.async_update_entity(
                e.entity_id, config_entry_id=entry.entry_id, device_id=device_id
            )
        except:
            LOGGER.warning("Failed to update entity %s", e, exc_info=True)


class MeshtasticDeviceClass(StrEnum):
    GATEWAY = "gateway"
    CHANNEL = "channel"


class MeshtasticEntity(Entity):
    _attr_device_class: MeshtasticDeviceClass

    def __init__(
        self,
        config_entry_id: str,
        node: int,
        meshtastic_class: MeshtasticDeviceClass,
        meshtastic_id: str = None,
    ) -> None:
        self._attr_meshtastic_class = meshtastic_class
        self._attr_unique_id = f"{config_entry_id}_{meshtastic_class}_{node}"
        if meshtastic_id is not None:
            self._attr_unique_id += f"_{meshtastic_id}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, node)})
        self._attr_device_class = meshtastic_class

    @property
    def suggested_object_id(self) -> str | None:
        suggested_id = super().suggested_object_id
        if suggested_id:
            return f"{self._attr_device_class} {suggested_id}"
        return f"{self._attr_device_class}"


MESHTASTIC_CLASS_SCHEMA = vol.All(vol.Lower, vol.Coerce(MeshtasticDeviceClass))


class GatewayEntity(MeshtasticEntity):
    _attr_icon = "mdi:radio-handheld"

    def __init__(
        self,
        config_entry_id: int,
        node: int,
        long_name: str,
        short_name: str,
        local_config: dict,
        module_config: dict,
    ) -> None:
        super().__init__(config_entry_id, node, MeshtasticDeviceClass.GATEWAY, None)
        self._local_config = local_config
        self._module_config = module_config
        self._short_name = short_name

        self._attr_name = "Node"
        self._attr_has_entity_name = True

        def flatten(dictionary, parent_key="", separator="_"):
            items = []
            for key, value in dictionary.items():
                new_key = parent_key + separator + key if parent_key else key
                if isinstance(value, MutableMapping):
                    items.extend(flatten(value, new_key, separator=separator).items())
                else:
                    items.append((new_key, value))
            return dict(items)

        attributes = {"config": local_config, "module": module_config}

        self._attr_extra_state_attributes = flatten(attributes)
        if self._attr_available:
            self._attr_state = "Connected"
        else:
            self._attr_state = "Disconnected"

    @property
    def suggested_object_id(self) -> str | None:
        return f"{self.device_class} {self._short_name}"


class GatewayChannelEntity(MeshtasticEntity):
    _attr_icon = "mdi:forum"

    def __init__(
        self,
        config_entry_id: str,
        gateway_node: int,
        gateway_entity: GatewayEntity,
        index: int,
        name: str,
        settings=dict,
        primary: bool = False,
        secondary: bool = False,
    ) -> None:
        super().__init__(
            config_entry_id, gateway_node, MeshtasticDeviceClass.CHANNEL, index
        )

        self._index = index
        self._attr_messages = []
        self._settings = settings
        self._gateway_suggested_id = gateway_entity.suggested_object_id

        self._attr_unique_id = (
            f"{config_entry_id}_{self.device_class}_{gateway_node}_{index}"
        )

        if name:
            self._attr_has_entity_name = True
            self._attr_name = name
        elif primary:
            self._attr_has_entity_name = True
            self._attr_name = "Primary"
        elif secondary:
            self._attr_has_entity_name = True
            self._attr_name = "Secondary"

        self._attr_name = "Channel " + self._attr_name

        self._attr_state = f"Channel #{index}"
        self._attr_should_poll = False
        self._attr_extra_state_attributes = {
            "node": gateway_node,
            "primary": primary,
            "secondary": secondary,
            "psk": self._settings["psk"],
            "uplink_enabled": self._settings["uplinkEnabled"],
            "downlink_enabled": self._settings["downlinkEnabled"],
        }

    @property
    def suggested_object_id(self) -> str | None:
        return f"{self._gateway_suggested_id} {self.name}"
