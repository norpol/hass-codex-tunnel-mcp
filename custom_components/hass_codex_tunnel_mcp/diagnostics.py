"""Diagnostics for OpenAI Tunnel for HA-MCP."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_API_KEY, CONF_HA_MCP_BEARER_TOKEN, CONF_HA_MCP_URL, DOMAIN
from .mcp_url import redact_mcp_url


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict:
    """Return redacted diagnostics."""
    runtime = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    data = {**entry.data, **entry.options}
    if CONF_API_KEY in data:
        data[CONF_API_KEY] = "**REDACTED**"
    if CONF_HA_MCP_BEARER_TOKEN in data:
        data[CONF_HA_MCP_BEARER_TOKEN] = "**REDACTED**"
    if CONF_HA_MCP_URL in data:
        data[CONF_HA_MCP_URL] = redact_mcp_url(str(data[CONF_HA_MCP_URL]))

    diagnostics = {"config": data}
    if runtime is not None:
        diagnostics["tunnel"] = runtime["tunnel"].status.__dict__.copy()
        updater = runtime.get("updater")
        if updater is not None:
            diagnostics["updater"] = updater.state.to_dict()
    return diagnostics
