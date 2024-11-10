from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.loader import Integration

    from .api import MeshtasticApiClient
    from .coordinator import MeshtasticDataUpdateCoordinator


type MeshtasticConfigEntry = ConfigEntry[MeshtasticData]


@dataclass
class MeshtasticData:
    client: MeshtasticApiClient
    coordinator: MeshtasticDataUpdateCoordinator
    integration: Integration
    gateway_node: dict
