"""OpenAI Tunnel for HA-MCP integration."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant, ServiceCall

from .binary import TunnelClientError, UnsupportedPlatformError, ensure_tunnel_client
from .const import (
    BIN_DIR_NAME,
    CONF_HA_MCP_BEARER_TOKEN,
    CONF_HA_MCP_URL,
    DOMAIN,
    PLATFORMS,
    RUN_DIR_NAME,
    STORAGE_DIR,
)
from .mcp_url import MCPUrlError, assess_mcp_url, async_probe_mcp_url
from .repairs import create_issue, delete_issue
from .tunnel import TunnelManager

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: "HomeAssistant", entry: "ConfigEntry") -> bool:
    """Set up OpenAI Tunnel for HA-MCP."""
    from homeassistant.exceptions import ConfigEntryNotReady

    hass.data.setdefault(DOMAIN, {})
    storage_dir = Path(hass.config.path(STORAGE_DIR))
    bin_root = storage_dir / BIN_DIR_NAME
    run_dir = storage_dir / RUN_DIR_NAME / entry.entry_id

    def notify() -> None:
        runtime = hass.data.get(DOMAIN, {}).get(entry.entry_id)
        if runtime is not None:
            for callback in runtime.get("listeners", []):
                callback()

    async def executable_provider(force: bool):
        return await ensure_tunnel_client(bin_root, force=force)

    tunnel = TunnelManager(executable_provider, run_dir, notify)
    hass.data[DOMAIN][entry.entry_id] = {
        "tunnel": tunnel,
        "listeners": [],
        "entry_data": _entry_data_factory(entry),
    }

    try:
        try:
            entry_data = {**entry.data, **entry.options}
            assessment = assess_mcp_url(str(entry_data[CONF_HA_MCP_URL]))
            await async_probe_mcp_url(
                assessment.url,
                bearer_token=str(entry_data.get(CONF_HA_MCP_BEARER_TOKEN) or ""),
            )
        except MCPUrlError as err:
            await create_issue(
                hass,
                "mcp_url_unreachable",
                "mcp_url_unreachable",
                {"url": assessment.redacted_url if "assessment" in locals() else ""},
            )
            raise
        if assessment.warning is not None:
            await create_issue(
                hass,
                "mcp_url_warning",
                assessment.warning,
                {"url": assessment.redacted_url},
            )
        else:
            await delete_issue(hass, "mcp_url_warning")

        try:
            await tunnel.start({**entry.data, **entry.options})
        except TunnelClientError as err:
            await create_issue(
                hass,
                "binary_download_failed",
                "binary_download_failed",
                {"error": str(err)},
            )
            raise
    except UnsupportedPlatformError as err:
        await create_issue(
            hass,
            "unsupported_arch",
            "unsupported_arch",
            {"platform": str(err)},
        )
        raise ConfigEntryNotReady(f"unsupported platform for tunnel-client: {err}") from err
    except Exception as err:
        _LOGGER.exception("Failed to start OpenAI Tunnel for HA-MCP")
        await create_issue(
            hass,
            "startup_failed",
            "startup_failed",
            {"error": str(err)},
        )
        raise ConfigEntryNotReady(str(err)) from err

    await delete_issue(hass, "startup_failed")
    await delete_issue(hass, "unsupported_arch")
    await delete_issue(hass, "binary_download_failed")
    await delete_issue(hass, "mcp_url_unreachable")
    await _async_register_services(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: "HomeAssistant", entry: "ConfigEntry") -> bool:
    """Unload the integration."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    runtime = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if runtime is not None:
        await runtime["tunnel"].stop()
    return unload_ok


async def _async_update_listener(hass: "HomeAssistant", entry: "ConfigEntry") -> None:
    """Reload when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def _async_register_services(hass: "HomeAssistant") -> None:
    """Register integration services once."""
    if hass.data[DOMAIN].get("_services_registered"):
        return

    async def restart_tunnel(call: "ServiceCall") -> None:
        for runtime in _matching_runtimes(hass, call):
            await runtime["tunnel"].restart(runtime["entry_data"]())

    async def redownload_tunnel_client(call: "ServiceCall") -> None:
        for runtime in _matching_runtimes(hass, call):
            await runtime["tunnel"].redownload(runtime["entry_data"]())

    hass.services.async_register(DOMAIN, "restart_tunnel", restart_tunnel)
    hass.services.async_register(
        DOMAIN, "redownload_tunnel_client", redownload_tunnel_client
    )
    hass.data[DOMAIN]["_services_registered"] = True


def _matching_runtimes(hass: "HomeAssistant", call: "ServiceCall"):
    entry_id = call.data.get("entry_id")
    for key, runtime in hass.data[DOMAIN].items():
        if key == "_services_registered":
            continue
        if entry_id is None or key == entry_id:
            yield runtime


def _entry_data_factory(entry: "ConfigEntry"):
    return lambda: {**entry.data, **entry.options}
