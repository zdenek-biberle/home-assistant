from logging import Logger, getLogger

LOGGER: Logger = getLogger(__package__)

DOMAIN = "meshtastic"

CONF_HOST = "host"
CONF_PORT = "port"

CONF_OPTION_FILTER_NODES = "nodes"
CONF_OPTION_NODE = "node"
CONF_OPTION_ADD_ANOTHER_NODE = "add_another_node"

SERVICE_SEND_TEXT = "send_text"
