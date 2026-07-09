"""Diagnostics for OpenAI Tunnel for HA-MCP."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_API_KEY, DOMAIN


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict:
    """Return redacted diagnostics."""
    runtime = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    data = {**entry.data, **entry.options}
    if CONF_API_KEY in data:
        data[CONF_API_KEY] = "**REDACTED**"

    diagnostics = {"config": data}
    if runtime is not None:
        diagnostics["mcp"] = runtime["mcp"].status.__dict__.copy()
        diagnostics["tunnel"] = runtime["tunnel"].status.__dict__.copy()
    return diagnostics
