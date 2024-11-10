from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, IntegrationError
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
)

import meshtastic.tcp_interface

from .api import (
    MeshtasticApiClient,
)
from .const import (
    CONF_OPTION_ADD_ANOTHER_NODE,
    CONF_OPTION_FILTER_NODES,
    CONF_OPTION_NODE,
    CONF_PORT,
    DOMAIN,
    LOGGER,
)

if TYPE_CHECKING:
    from typing import Any

    from homeassistant.core import HomeAssistant
    from homeassistant.data_entry_flow import FlowResult


_LOGGER = LOGGER.getChild(__name__)


def _step_user_data_schema_factory(
    host="", port=meshtastic.tcp_interface.DEFAULT_TCP_PORT
):
    return vol.Schema(
        {
            vol.Required(CONF_HOST, default=host): str,
            vol.Required(CONF_PORT, default=port): int,
        }
    )


STEP_USER_DATA_SCHEMA = _step_user_data_schema_factory()

NODE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_OPTION_NODE): cv.string,
        vol.Optional(CONF_OPTION_ADD_ANOTHER_NODE): cv.boolean,
    }
)


def _build_add_node_schema(
    options: dict[str, Any], nodes: dict[int, Any], node_selection_required=True
):
    already_selected_node_nums = [el["id"] for el in options[CONF_OPTION_FILTER_NODES]]
    selectable_nodes = {
        node_id: node_info
        for node_id, node_info in nodes.items()
        if node_id not in already_selected_node_nums
    }
    if not selectable_nodes:
        return vol.Schema({})

    selector_options = [
        SelectOptionDict(value=str(node_id), label=node_info["user"]["longName"])
        for node_id, node_info in sorted(
            selectable_nodes.items(),
            key=lambda el: el[1]["isFavorite"] if "isFavorite" in el[1] else False,
            reverse=True,
        )
    ]

    return vol.Schema(
        {
            vol.Required(CONF_OPTION_NODE)
            if node_selection_required
            else vol.Optional(CONF_OPTION_NODE): SelectSelector(
                SelectSelectorConfig(options=selector_options)
            ),
            vol.Optional(CONF_OPTION_ADD_ANOTHER_NODE): cv.boolean,
        }
    )


async def validate_input_for_device(
    hass: HomeAssistant, data: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate the user input allows us to connect."""
    client = MeshtasticApiClient(
        hostname=f"{data[CONF_HOST]}:{data[CONF_PORT]}", hass=hass, config_entry_id=None
    )

    try:
        await client.connect()
        gateway_node = await client.async_get_own_node()
        nodes = await client.async_get_all_nodes()
        return gateway_node, nodes
    except IntegrationError as e:
        _LOGGER.error("Failed to connect to meshtastic device")
        raise CannotConnect from e


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Resol."""

    VERSION = 1

    # Make sure user input data is passed from one step to the next using user_input_from_step_user
    def __init__(self):
        self.user_input_from_step_user = None

    # This is step 1 for the host/port/user/pass function.
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                gateway_node, nodes = await validate_input_for_device(
                    self.hass, user_input
                )
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                # Checks that the device is actually unique, otherwise abort
                await self.async_set_unique_id(str(gateway_node["num"]))
                self._abort_if_unique_id_configured(
                    updates={
                        CONF_HOST: user_input[CONF_HOST],
                        CONF_PORT: user_input[CONF_PORT],
                    }
                )

                # Before creating the entry in the config_entry registry, go to step 2 for the options
                # However, make sure the steps from the user input are passed on to the next step
                self.user_input_from_step_user = user_input
                self.gateway_node = gateway_node
                self.nodes = nodes
                self.data = user_input
                self.options = {CONF_OPTION_FILTER_NODES: []}

                # Now call the second step but set user_input to None for the first time to force data entry in step 2
                return await self.async_step_node(user_input=None)

        # Show the form for step 1 with the user/host/pass as defined in STEP_USER_DATA_SCHEMA
        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )

    async def async_step_node(self, user_input: dict[str, Any] | None = None):
        """Second step in config flow to add a repo to watch."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                node_id = int(user_input[CONF_OPTION_NODE])
                if node_id not in self.nodes:
                    raise ValueError("Unknown node")
            except ValueError:
                errors["base"] = "invalid_node"

            if not errors:
                self.options[CONF_OPTION_FILTER_NODES].append(
                    {
                        "id": node_id,
                        "name": self.nodes[node_id]["user"]["longName"],
                    }
                )
                if user_input.get(CONF_OPTION_ADD_ANOTHER_NODE, False):
                    return await self.async_step_node()
                return self.async_create_entry(
                    title=self.gateway_node["user"]["longName"],
                    data=self.data,
                    options=self.options,
                )

        schema = _build_add_node_schema(self.options, self.nodes)
        return self.async_show_form(step_id="node", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        return OptionsFlowHandler(config_entry)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None):
        """Add reconfigure step to allow to reconfigure a config entry."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                # Validate the user input whilst setting up integration or adding new devices.
                gateway_node, nodes = await validate_input_for_device(
                    self.hass, user_input
                )
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(str(gateway_node["num"]))

                self.gateway_node = gateway_node
                self.nodes = nodes
                self.data = user_input
                self.options = {CONF_OPTION_FILTER_NODES: []}

            if not errors:
                return await self.async_step_node(user_input=None)
                # return self.hass.config_entries.async_update_entry(self._get_reconfigure_entry(), data=self.data)
                # return self.async_create_entry(title=self.gateway_node['user']['longName'], data=self.data, options=self.options)
        else:
            data_schema = STEP_USER_DATA_SCHEMA
            config_entry = self.hass.config_entries.async_get_entry(
                self.context.get("entry_id", None)
            )
            if config_entry:
                data_schema = _step_user_data_schema_factory(
                    config_entry.data.get(CONF_HOST, ""),
                    config_entry.data.get(
                        CONF_PORT, meshtastic.tcp_interface.DEFAULT_TCP_PORT
                    ),
                )

            return self.async_show_form(
                step_id="reconfigure", data_schema=data_schema, errors=errors
            )


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handles options flow for the component."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self.config_entry = config_entry
        self.options = {}
        self.nodes = None

    async def async_step_init(
        self, user_input: dict[str, Any] = None
    ) -> dict[str, Any]:
        """Manage the options for the custom component."""
        errors: dict[str, str] = {}
        if self.nodes is None:
            try:
                _, self.nodes = await validate_input_for_device(
                    self.hass, self.config_entry.data
                )
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"

        if errors:
            return self.async_show_form(step_id="init", errors=errors)

        current_filter_node_option = (
            self.options[CONF_OPTION_FILTER_NODES]
            if CONF_OPTION_FILTER_NODES in self.options
            else self.config_entry.options[CONF_OPTION_FILTER_NODES]
        )
        all_nodes = {str(k): v["user"]["longName"] for k, v in self.nodes.items()}
        already_selected_node_ids = [el["id"] for el in current_filter_node_option]

        if user_input is not None:
            updated_filter_node_option = deepcopy(current_filter_node_option)

            removed_node_ids = [
                node_id
                for node_id in already_selected_node_ids
                if str(node_id) not in user_input[CONF_OPTION_FILTER_NODES]
            ]
            for node_id in removed_node_ids:
                updated_filter_node_option = [
                    e for e in updated_filter_node_option if e["id"] != node_id
                ]

            if user_input.get(CONF_OPTION_NODE):
                # Add the new node
                updated_filter_node_option.append(
                    {
                        "id": int(user_input[CONF_OPTION_NODE]),
                        "name": self.nodes[int(user_input[CONF_OPTION_NODE])]["user"][
                            "longName"
                        ],
                    }
                )

            if user_input.get(CONF_OPTION_ADD_ANOTHER_NODE, False):
                self.options[CONF_OPTION_FILTER_NODES] = updated_filter_node_option
                return await self.async_step_init()
            else:
                return self.async_create_entry(
                    title="",
                    data={CONF_OPTION_FILTER_NODES: updated_filter_node_option},
                )

        else:
            selected_nodes = {
                str(node_id): all_nodes.get(str(node_id), f"Unknown (id: {node_id})")
                for node_id in already_selected_node_ids
            }
            options_schema = vol.Schema(
                {
                    vol.Required(
                        CONF_OPTION_FILTER_NODES, default=list(selected_nodes.keys())
                    ): cv.multi_select(selected_nodes),
                    **_build_add_node_schema(
                        self.options
                        if CONF_OPTION_FILTER_NODES in self.options
                        else self.config_entry.options,
                        self.nodes,
                        node_selection_required=False,
                    ).schema,
                }
            )
            return self.async_show_form(
                step_id="init", data_schema=options_schema, errors=errors
            )
