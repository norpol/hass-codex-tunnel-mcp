"""Constants for OpenAI Tunnel for HA-MCP."""

from __future__ import annotations

DOMAIN = "hass_codex_tunnel_mcp"
NAME = "OpenAI Tunnel for HA-MCP"

CONF_TUNNEL_ID = "tunnel_id"
CONF_API_KEY = "api_key"
CONF_HA_MCP_URL = "ha_mcp_url"
CONF_CONTROL_PLANE_BASE_URL = "control_plane_base_url"
CONF_CONTROL_PLANE_PATH = "control_plane_url_path"

DEFAULT_CONTROL_PLANE_BASE_URL = ""
DEFAULT_CONTROL_PLANE_PATH = ""

TUNNEL_CLIENT_VERSION = "v0.0.10"
TUNNEL_ID_RE = r"^tunnel_[0-9a-f]{32}$"

STORAGE_DIR = ".hass_codex_tunnel_mcp"
RUN_DIR_NAME = "run"
BIN_DIR_NAME = "bin"

PLATFORMS = ["sensor"]

ATTR_TUNNEL_STATE = "tunnel_state"
ATTR_TUNNEL_HEALTHY = "tunnel_healthy"
ATTR_TUNNEL_CLIENT_VERSION = "tunnel_client_version"
ATTR_LAST_ERROR = "last_error"
