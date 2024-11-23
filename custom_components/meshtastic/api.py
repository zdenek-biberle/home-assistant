"""Sample API Client."""

from __future__ import annotations

import asyncio
import base64
import inspect
import json
from copy import deepcopy
from enum import StrEnum
from functools import wraps
from typing import Any

from google.protobuf.json_format import MessageToJson
from google.protobuf.message import Message
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import IntegrationError
from pubsub import pub

import meshtastic.util
from meshtastic import BROADCAST_ADDR, tcp_interface
from meshtastic.mesh_interface import MeshInterface

from .const import DOMAIN, LOGGER

original_our_exit = meshtastic.util.our_exit
_LOGGER = LOGGER.getChild(__name__)

_api_errors = []


# hack to not terminate home assistant on meshtastic failures: https://github.com/meshtastic/python/issues/703
def patched_our_exit(message, return_value=1) -> None:
    _api_errors.append((return_value, message))
    _LOGGER.debug(
        "meshtastic lib attempted to exit process. code=%d, message=%s",
        return_value,
        message,
    )


meshtastic.util.our_exit = patched_our_exit
meshtastic.mesh_interface.our_exit = patched_our_exit


EVENT_MESHTASTIC_API_BASE = f"{DOMAIN}_api"
EVENT_MESHTASTIC_API_NODE_UPDATED = EVENT_MESHTASTIC_API_BASE + "_node_updated"
EVENT_MESHTASTIC_API_TELEMETRY = EVENT_MESHTASTIC_API_BASE + "_telemetry"
EVENT_MESHTASTIC_API_PACKET = EVENT_MESHTASTIC_API_BASE + "_packet"
EVENT_MESHTASTIC_API_TEXT_MESSAGE = EVENT_MESHTASTIC_API_BASE + "_text_message"

ATTR_EVENT_MESHTASTIC_API_CONFIG_ENTRY_ID = "config_entry_id"
ATTR_EVENT_MESHTASTIC_API_NODE = "node"
ATTR_EVENT_MESHTASTIC_API_DATA = "data"
ATTR_EVENT_MESHTASTIC_API_TELEMETRY_TYPE = "telemetry_type"


class EventMeshtasticApiTelemetryType(StrEnum):
    DEVICE_METRICS = "device_metrics"
    LOCAL_STATS = "local_stats"
    ENVIRONMENT_METRICS = "environment_metrics"
    POWER_METRICS = "power_metrics"


class MeshtasticApiClientError(IntegrationError):
    """Exception to indicate a general API error."""


class MeshtasticApiClientCommunicationError(
    MeshtasticApiClientError,
):
    """Exception to indicate a communication error."""


def pubsub_callback(f):
    """Decorator for pubsub callbacks making sure that event is for proper interface and ensure thread safety"""

    @wraps(f)
    def wrapper(self, interface, *args, **kwargs):
        # only react to callback from our own instance (meshtastic callbacks are shared globally)
        if interface != self._interface:
            return

        async def run(coro):
            self._hass.async_create_background_task(coro, name="meshtastic-api")

        # ensure thread safety as meshtastic library uses threading
        asyncio.run_coroutine_threadsafe(run(f(self, *args, **kwargs)), self._hass.loop)

    # adjust method signature (add interface param) so that pubsub library is happy
    try:
        f_signature = inspect.signature(f)
        modified_parameters = list(f_signature.parameters.values())
        # append after self as second argument
        modified_parameters.insert(
            1,
            inspect.Parameter(
                "interface", kind=inspect.Parameter.POSITIONAL_OR_KEYWORD
            ),
        )
        wrapper.__signature__ = f_signature.replace(parameters=modified_parameters)
    except:
        _LOGGER.info("Failed to modify pubsub callback signature", exc_info=True)

    return wrapper


class MeshtasticApiClient:
    def __init__(
        self, hostname: str, hass: HomeAssistant, config_entry_id: str
    ) -> None:
        self._logger = LOGGER.getChild(self.__class__.__name__)
        self._connected = asyncio.Event()
        self._hass = hass
        self._config_entry_id = config_entry_id

        if ":" in hostname:
            self._host, self._port = hostname.split(":")
        else:
            self._host = hostname
            self._port = tcp_interface.DEFAULT_TCP_PORT

        pub.subscribe(
            self._pubsub_on_connection_established, "meshtastic.connection.established"
        )
        pub.subscribe(self._pubsub_on_connection_lost, "meshtastic.connection.lost")
        pub.subscribe(self._pubsub_on_receive, "meshtastic.receive")
        pub.subscribe(self._pubsub_on_node_updated, "meshtastic.node.updated")

        # we can't connect directly as implementation blocks thread
        self._interface = tcp_interface.TCPInterface(
            self._host, portNumber=self._port, connectNow=False
        )

    async def connect(self):
        if self._connected.is_set():
            return
        # meshtastic library used blocking code (sleep) during connection, so schedule connect in executor
        self._hass.async_add_executor_job(self._connect)
        await asyncio.wait_for(self._connected.wait(), 30)

    def _connect(self) -> None:
        try:
            # setup tcp socket (myConnect is only called when connectNow=True)
            self._interface.myConnect()
            # actual connection
            self._interface.connect()
        except MeshInterface.MeshInterfaceError as e:
            self._logger.warning("Connection failed")
            raise MeshtasticApiClientCommunicationError("Failed to connect") from e

    @pubsub_callback
    async def _pubsub_on_receive(self, packet):
        await self._process_meshtastic_packet(packet)

    @pubsub_callback
    async def _pubsub_on_connection_established(self):
        self._logger.debug("Connection established")

        self._connected.set()

    @pubsub_callback
    def _pubsub_on_connection_lost(self):
        self._logger.debug("Connection lost")
        self._connected.clear()

    @pubsub_callback
    async def _pubsub_on_node_updated(self, node):  # called when a packet arrives
        node_clone = deepcopy(node)
        self._logger.debug(f"Node updated: {node}")
        node_update_data = self._clean_packet_dict(node_clone)
        self._hass.bus.async_fire(
            EVENT_MESHTASTIC_API_NODE_UPDATED,
            self._build_event_data(node["num"], node_update_data),
        )

    def _build_event_data(self, node_id: int, data):
        return {"config_entry_id": self._config_entry_id, "node": node_id, "data": data}

    def _clean_packet_dict(self, obj):
        """Helper to produce event that is serializable"""
        for k, v in obj.items():
            if isinstance(v, dict):
                obj[k] = self._clean_packet_dict(v)
            elif isinstance(v, bytes):
                obj[k] = base64.b64encode(v).decode("utf-8")

        # remove raw protobuf objects
        if "raw" in obj:
            del obj["raw"]

        return obj

    async def _process_meshtastic_packet(self, packet: dict[str, Any]) -> None:
        packet_clone = deepcopy(packet)
        packet_clone = self._clean_packet_dict(packet_clone)
        self._logger.debug(f"Received Packet: {packet_clone}")
        node_id = packet_clone["from"]
        self._hass.bus.async_fire(
            EVENT_MESHTASTIC_API_PACKET, self._build_event_data(node_id, packet_clone)
        )

        if "decoded" not in packet_clone:
            return
        decoded = packet_clone["decoded"]
        portnum = decoded["portnum"]
        if portnum == "TELEMETRY_APP":
            telemetry = decoded["telemetry"]
            device_metrics = telemetry.get("deviceMetrics", None)
            local_stats = telemetry.get("localStats", None)
            environment_metrics = telemetry.get("environmentMetrics", None)
            power_metrics = telemetry.get("powerMetrics", None)

            if device_metrics:
                event_data = self._build_event_data(node_id, device_metrics)
                event_data["node_info"] = {
                    "name": self._interface.nodesByNum[node_id]["user"]["longName"]
                }
                event_data[ATTR_EVENT_MESHTASTIC_API_TELEMETRY_TYPE] = (
                    EventMeshtasticApiTelemetryType.DEVICE_METRICS
                )
                self._hass.bus.async_fire(EVENT_MESHTASTIC_API_TELEMETRY, event_data)

            if local_stats:
                event_data = self._build_event_data(node_id, local_stats)
                event_data["node_info"] = {
                    "name": self._interface.nodesByNum[node_id]["user"]["longName"]
                }
                event_data[ATTR_EVENT_MESHTASTIC_API_TELEMETRY_TYPE] = (
                    EventMeshtasticApiTelemetryType.LOCAL_STATS
                )
                self._hass.bus.async_fire(EVENT_MESHTASTIC_API_TELEMETRY, event_data)

            if environment_metrics:
                event_data = self._build_event_data(node_id, environment_metrics)
                event_data["node_info"] = {
                    "name": self._interface.nodesByNum[node_id]["user"]["longName"]
                }
                event_data[ATTR_EVENT_MESHTASTIC_API_TELEMETRY_TYPE] = (
                    EventMeshtasticApiTelemetryType.ENVIRONMENT_METRICS
                )
                self._hass.bus.async_fire(EVENT_MESHTASTIC_API_TELEMETRY, event_data)

            if power_metrics:
                event_data = self._build_event_data(node_id, power_metrics)
                event_data["node_info"] = {
                    "name": self._interface.nodesByNum[node_id]["user"]["longName"]
                }
                event_data[ATTR_EVENT_MESHTASTIC_API_TELEMETRY_TYPE] = (
                    EventMeshtasticApiTelemetryType.POWER_METRICS
                )
                self._hass.bus.async_fire(EVENT_MESHTASTIC_API_TELEMETRY, event_data)

        elif portnum == "TEXT_MESSAGE_APP":
            event_data = self._build_event_data(
                node_id,
                {
                    "from": packet_clone["from"],
                    "fromId": packet_clone["fromId"],
                    "to": packet_clone["to"],
                    "toId": packet_clone["toId"],
                    "message": decoded["text"],
                },
            )
            event_data["message_id"] = packet_clone["id"]
            self._hass.bus.async_fire(EVENT_MESHTASTIC_API_TEXT_MESSAGE, event_data)
        else:
            self._logger.debug("Unhandled portnum %s", portnum)

    async def async_get_channels(self) -> list[dict]:
        return [
            json.loads(self._message_to_json(c))
            for c in self._interface.localNode.channels
        ]

    async def async_get_node_local_config(self) -> dict:
        return json.loads(
            self._message_to_json(self._interface.localNode.localConfig)
        )

    async def async_get_node_module_config(self) -> dict:
        return json.loads(
            self._message_to_json(self._interface.localNode.moduleConfig)
        )

    def _message_to_json(self, message: Message) -> str:
        try:
            return MessageToJson(message, always_print_fields_with_no_presence=True)
        except TypeError:
            # older protobuf version
            return MessageToJson(message, including_default_value_fields=True)


    async def async_get_own_node(self) -> Any:
        return self._interface.getMyNodeInfo() or {}

    async def async_get_all_nodes(self) -> dict:
        return self._interface.nodesByNum or {}

    async def send_text(
        self,
        text: str,
        destination_id: int | str = BROADCAST_ADDR,
        want_ack: bool = False,
        want_response: bool = False,
        channel_index: int = 0,
    ):
        _api_errors.clear()
        packet = self._interface.sendText(
            text,
            destinationId=destination_id,
            wantAck=want_ack,
            wantResponse=want_response,
            channelIndex=channel_index,
        )
        # poor workaround to see if call had a failure.
        if _api_errors:
            api_error_code, api_error_message = _api_errors.pop()
            raise MeshtasticApiClientCommunicationError(api_error_message)

        return True

    def close(self):
        self._interface.close()
        self._connected.clear()
