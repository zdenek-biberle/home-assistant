from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.components.sensor import (
    DOMAIN as SENSOR_DOMAIN,
)
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config import callback
from homeassistant.const import (
    PERCENTAGE,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfTime,
)

from . import LOGGER, helpers
from .entity import MeshtasticNodeEntity

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback
    from homeassistant.helpers.typing import StateType

    from .coordinator import MeshtasticDataUpdateCoordinator
    from .data import MeshtasticConfigEntry


def _build_sensors(nodes, coordinator):
    entities = []
    entities += _build_device_sensors(nodes, coordinator)
    entities += _build_local_stats_sensors(nodes, coordinator)
    entities += _build_power_metrics_sensors(nodes, coordinator)
    return entities


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MeshtasticConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    await helpers.setup_platform_entry(hass, entry, async_add_entities, _build_sensors)


async def async_unload_entry(
    hass: HomeAssistant,
    entry: MeshtasticConfigEntry,
) -> bool:
    return await helpers.async_unload_entry(hass, entry)


@dataclass(kw_only=True)
class MeshtasticSensorEntityDescription(SensorEntityDescription):
    exists_fn: Callable[[MeshtasticSensor], bool] = lambda _: True
    value_fn: Callable[[MeshtasticSensor], StateType]


class MeshtasticSensor(MeshtasticNodeEntity, SensorEntity):
    entity_description: MeshtasticSensorEntityDescription

    def __init__(
        self,
        coordinator: MeshtasticDataUpdateCoordinator,
        entity_description: MeshtasticSensorEntityDescription,
        node_id: int,
    ) -> None:
        super().__init__(coordinator, node_id, SENSOR_DOMAIN, entity_description)

    @callback
    def _async_update_attrs(self) -> None:
        LOGGER.debug("Updating sensor attributes: %s", self)
        self._attr_native_value = self.entity_description.value_fn(self)


def _build_device_sensors(nodes, coordinator):
    entities = []

    entities += [
        MeshtasticSensor(
            coordinator=coordinator,
            entity_description=MeshtasticSensorEntityDescription(
                key="device_uptime",
                name="Uptime",
                icon="mdi:progress-clock",
                native_unit_of_measurement=UnitOfTime.SECONDS,
                device_class=SensorDeviceClass.DURATION,
                state_class=SensorStateClass.TOTAL_INCREASING,
                value_fn=lambda device: device.coordinator.data[device.node_id]
                .get("deviceMetrics", {})
                .get("uptimeSeconds", None),
            ),
            node_id=node_id,
        )
        for node_id, node_info in nodes.items()
    ]

    def battery_level(device):
        level = (
            device.coordinator.data[device.node_id]
            .get("deviceMetrics", {})
            .get("batteryLevel", None)
        )
        if level is not None:
            return max(0, min(100, level))
        return level

    entities += [
        MeshtasticSensor(
            coordinator=coordinator,
            entity_description=MeshtasticSensorEntityDescription(
                key="device_battery_level",
                name="Battery Level",
                icon="mdi:battery",
                native_unit_of_measurement=PERCENTAGE,
                device_class=SensorDeviceClass.BATTERY,
                state_class=SensorStateClass.MEASUREMENT,
                value_fn=battery_level,
            ),
            node_id=node_id,
        )
        for node_id, node_info in nodes.items()
    ]

    entities += [
        MeshtasticSensor(
            coordinator=coordinator,
            entity_description=MeshtasticSensorEntityDescription(
                key="device_voltage",
                name="Voltage",
                icon="mdi:current-dc",
                native_unit_of_measurement=UnitOfElectricPotential.VOLT,
                device_class=SensorDeviceClass.VOLTAGE,
                state_class=SensorStateClass.MEASUREMENT,
                value_fn=lambda device: device.coordinator.data[device.node_id]
                .get("deviceMetrics", {})
                .get("voltage", None),
            ),
            node_id=node_id,
        )
        for node_id, node_info in nodes.items()
    ]
    entities += [
        MeshtasticSensor(
            coordinator=coordinator,
            entity_description=MeshtasticSensorEntityDescription(
                key="device_channel_utilization",
                name="Channel Utilization",
                icon="mdi:radio-tower",
                native_unit_of_measurement=PERCENTAGE,
                state_class=SensorStateClass.MEASUREMENT,
                value_fn=lambda device: device.coordinator.data[device.node_id]
                .get("deviceMetrics", {})
                .get("channelUtilization", None),
            ),
            node_id=node_id,
        )
        for node_id, node_info in nodes.items()
    ]
    entities += [
        MeshtasticSensor(
            coordinator=coordinator,
            entity_description=MeshtasticSensorEntityDescription(
                key="device_airtime",
                name="Airtime",
                icon="mdi:timer",
                native_unit_of_measurement=PERCENTAGE,
                state_class=SensorStateClass.MEASUREMENT,
                value_fn=lambda device: device.coordinator.data[device.node_id]
                .get("deviceMetrics", {})
                .get("airUtilTx", None),
            ),
            node_id=node_id,
        )
        for node_id, node_info in nodes.items()
    ]

    return entities


def _build_local_stats_sensors(nodes, coordinator) -> list[MeshtasticSensor]:
    nodes_with_loca_stats = {
        node_id: node_info
        for node_id, node_info in nodes.items()
        if "localStats" in node_info
    }

    entities = []
    try:
        entities += [
            MeshtasticSensor(
                coordinator=coordinator,
                entity_description=MeshtasticSensorEntityDescription(
                    key="stats_packets_tx",
                    name="Packets sent",
                    icon="mdi:call-made",
                    state_class=SensorStateClass.TOTAL_INCREASING,
                    value_fn=lambda device: device.coordinator.data[device.node_id][
                        "localStats"
                    ].get("numPacketsTx", 0),
                ),
                node_id=node_id,
            )
            for node_id, node_info in nodes_with_loca_stats.items()
        ]

        entities += [
            MeshtasticSensor(
                coordinator=coordinator,
                entity_description=MeshtasticSensorEntityDescription(
                    key="stats_packets_rx",
                    name="Packets received",
                    icon="mdi:call-received",
                    state_class=SensorStateClass.TOTAL_INCREASING,
                    value_fn=lambda device: device.coordinator.data[device.node_id][
                        "localStats"
                    ].get("numPacketsRx", 0),
                ),
                node_id=node_id,
            )
            for node_id, node_info in nodes_with_loca_stats.items()
        ]

        entities += [
            MeshtasticSensor(
                coordinator=coordinator,
                entity_description=MeshtasticSensorEntityDescription(
                    key="stats_packets_rx_bad",
                    name="Malformed Packets received",
                    icon="mdi:call-missed",
                    state_class=SensorStateClass.TOTAL_INCREASING,
                    value_fn=lambda device: device.coordinator.data[device.node_id][
                        "localStats"
                    ].get("numPacketsRxBad", 0),
                ),
                node_id=node_id,
            )
            for node_id, node_info in nodes_with_loca_stats.items()
        ]

        entities += [
            MeshtasticSensor(
                coordinator=coordinator,
                entity_description=MeshtasticSensorEntityDescription(
                    key="stats_packets_rx_duplicate",
                    name="Duplicate Packets received",
                    icon="mdi:call-split",
                    state_class=SensorStateClass.TOTAL_INCREASING,
                    value_fn=lambda device: device.coordinator.data[device.node_id][
                        "localStats"
                    ].get("numRxDupe", 0),
                ),
                node_id=node_id,
            )
            for node_id, node_info in nodes_with_loca_stats.items()
        ]

        entities += [
            MeshtasticSensor(
                coordinator=coordinator,
                entity_description=MeshtasticSensorEntityDescription(
                    key="stats_packets_tx_relayed",
                    name="Packets relayed",
                    icon="mdi:call-missed",
                    state_class=SensorStateClass.TOTAL_INCREASING,
                    value_fn=lambda device: device.coordinator.data[device.node_id][
                        "localStats"
                    ].get("numTxRelay", 0),
                ),
                node_id=node_id,
            )
            for node_id, node_info in nodes_with_loca_stats.items()
        ]

        entities += [
            MeshtasticSensor(
                coordinator=coordinator,
                entity_description=MeshtasticSensorEntityDescription(
                    key="stats_packets_tx_relay_cancelled",
                    name="Packets relay canceled",
                    icon="mdi:call-missed",
                    state_class=SensorStateClass.TOTAL_INCREASING,
                    value_fn=lambda device: device.coordinator.data[device.node_id][
                        "localStats"
                    ].get("numTxRelayCanceled", 0),
                ),
                node_id=node_id,
            )
            for node_id, node_info in nodes_with_loca_stats.items()
        ]

        entities += [
            MeshtasticSensor(
                coordinator=coordinator,
                entity_description=MeshtasticSensorEntityDescription(
                    key="stats_nodes_online",
                    name="Online Nodes",
                    icon="mdi:radio-handheld",
                    state_class=SensorStateClass.TOTAL,
                    value_fn=lambda device: device.coordinator.data[device.node_id][
                        "localStats"
                    ].get("numOnlineNodes", 0),
                ),
                node_id=node_id,
            )
            for node_id, node_info in nodes_with_loca_stats.items()
        ]

        entities += [
            MeshtasticSensor(
                coordinator=coordinator,
                entity_description=MeshtasticSensorEntityDescription(
                    key="stats_nodes_total",
                    name="Total Nodes",
                    icon="mdi:radio-handheld",
                    state_class=SensorStateClass.TOTAL,
                    value_fn=lambda device: device.coordinator.data[device.node_id][
                        "localStats"
                    ].get("numTotalNodes", 0),
                ),
                node_id=node_id,
            )
            for node_id, node_info in nodes_with_loca_stats.items()
        ]
    except:
        LOGGER.warning("Failed to create local stats entities", exc_info=True)

    return entities


def _build_power_metrics_sensors(nodes, coordinator) -> list[MeshtasticSensor]:
    nodes_with_power_metrics = {
        node_id: node_info
        for node_id, node_info in nodes.items()
        if "powerMetrics" in node_info
    }
    if not nodes_with_power_metrics:
        return []

    entities = []
    try:
        for node_id, node_info in nodes_with_power_metrics.items():
            power_metrics = node_info["powerMetrics"]
            for channel in range(1, 3):
                voltage_key = f"ch{channel}Voltage"
                current_key = f"ch{channel}Current"

                def power_metrics_value_fn(key):
                    return (
                        lambda device: device.coordinator.data[device.node_id]
                        .get("powerMetrics", {})
                        .get(key, None)
                    )

                if voltage_key in power_metrics:
                    entities.append(
                        MeshtasticSensor(
                            coordinator=coordinator,
                            entity_description=MeshtasticSensorEntityDescription(
                                key=f"power_ch{channel}_voltage",
                                name=f"Channel {channel} Voltage",
                                icon="mdi:current-dc",
                                native_unit_of_measurement=UnitOfElectricPotential.VOLT,
                                device_class=SensorDeviceClass.VOLTAGE,
                                state_class=SensorStateClass.MEASUREMENT,
                                value_fn=power_metrics_value_fn(voltage_key),
                            ),
                            node_id=node_id,
                        )
                    )
                if current_key in power_metrics:
                    entities.append(
                        MeshtasticSensor(
                            coordinator=coordinator,
                            entity_description=MeshtasticSensorEntityDescription(
                                key=f"power_ch{channel}_current",
                                name=f"Channel {channel} Current",
                                icon="mdi:current-dc",
                                native_unit_of_measurement=UnitOfElectricCurrent.MILLIAMPERE,
                                device_class=SensorDeviceClass.CURRENT,
                                state_class=SensorStateClass.MEASUREMENT,
                                value_fn=power_metrics_value_fn(current_key),
                            ),
                            node_id=node_id,
                        )
                    )

    except:
        LOGGER.warning("Failed to create power metrics entities", exc_info=True)

    return entities
