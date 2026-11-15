"""Exercise MCP startup failures through Home Assistant's config-entry lifecycle."""

from __future__ import annotations

from io import BytesIO
import logging
from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock, Mock
from urllib.error import HTTPError, URLError

import pytest
import pytest_asyncio
from homeassistant import loader
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.helpers import frame, issue_registry as ir
from homeassistant.helpers.storage import Store

from custom_components import hass_codex_tunnel_mcp as integration
from custom_components.hass_codex_tunnel_mcp import config_flow, mcp_url
from custom_components.hass_codex_tunnel_mcp.const import (
    CONF_HA_MCP_BEARER_TOKEN,
    CONF_HA_MCP_URL,
    DOMAIN,
)
from custom_components.hass_codex_tunnel_mcp.repairs import create_issue

MCP_URL = "http://127.0.0.1:8123/test-secret-path?token=test-secret-query"


def http_error(status):
    return HTTPError(MCP_URL, status, "test-private-detail", {}, BytesIO())


@pytest_asyncio.fixture
async def setup_context(tmp_path, monkeypatch):
    hass = HomeAssistant(str(tmp_path))
    hass.set_state(CoreState.starting)
    monkeypatch.setattr(frame._hass, "hass", hass)
    monkeypatch.setattr(Store, "async_delay_save", Mock())
    hass.config_entries = Mock(
        async_forward_entry_setups=AsyncMock(),
        async_unload_platforms=AsyncMock(return_value=True),
    )
    loaded_integration = Mock(
        domain=DOMAIN,
        async_get_component=AsyncMock(return_value=integration),
        async_get_platform=AsyncMock(return_value=config_flow),
        logger=logging.getLogger(integration.__name__),
    )
    monkeypatch.setattr(
        loader, "async_get_integration", AsyncMock(return_value=loaded_integration)
    )
    entry = ConfigEntry(
        entry_id="test-entry",
        domain=DOMAIN,
        title="Test tunnel",
        data={CONF_HA_MCP_URL: MCP_URL},
        options={CONF_HA_MCP_BEARER_TOKEN: "test-ha-token"},
        version=1,
        minor_version=1,
        source="user",
        unique_id="test-tunnel",
        discovery_keys=MappingProxyType({}),
        subentries_data=None,
    )
    entry.supports_unload = True
    entry.supports_remove_device = False
    tunnel = Mock(start=AsyncMock(), stop=AsyncMock())
    updater = Mock(
        async_load=AsyncMock(),
        async_start_auto_update=AsyncMock(),
        async_stop_auto_update=AsyncMock(),
    )
    monkeypatch.setattr(integration, "TunnelManager", Mock(return_value=tunnel))
    monkeypatch.setattr(integration, "TunnelClientUpdater", Mock(return_value=updater))
    registry = ir.async_get(hass)

    yield SimpleNamespace(
        hass=hass, entry=entry, tunnel=tunnel, updater=updater, registry=registry
    )

    entry.async_cancel_retry_setup()
    await entry._async_process_on_unload(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    hass.import_executor.shutdown(wait=True)


@pytest.mark.asyncio
async def test_route_appearing_after_startup_retries_automatically(
    setup_context, monkeypatch, caplog
):
    ctx = setup_context
    caplog.set_level(logging.DEBUG, logger=integration.__name__)
    probe = Mock(side_effect=[http_error(404), http_error(405)])
    monkeypatch.setattr(mcp_url, "urlopen", probe)
    other_runtime = object()
    ctx.hass.data[DOMAIN] = {"other-entry": other_runtime}

    await ctx.entry.async_setup_locked(ctx.hass)

    assert ctx.entry.state is ConfigEntryState.SETUP_RETRY
    assert ctx.entry.reason == "MCP endpoint returned HTTP 404"
    assert ctx.hass.data[DOMAIN] == {"other-entry": other_runtime}
    assert not ctx.registry.issues
    ctx.tunnel.start.assert_not_awaited()
    ctx.updater.async_start_auto_update.assert_not_awaited()
    ctx.hass.config_entries.async_forward_entry_setups.assert_not_awaited()

    ctx.hass.set_state(CoreState.running)
    ctx.hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await ctx.hass.async_block_till_done(wait_background_tasks=True)

    assert probe.call_count == 2
    assert ctx.entry.state is ConfigEntryState.LOADED
    assert ctx.entry.entry_id in ctx.hass.data[DOMAIN]
    ctx.tunnel.start.assert_awaited_once()
    ctx.updater.async_start_auto_update.assert_awaited_once()
    ctx.hass.config_entries.async_forward_entry_setups.assert_awaited_once()
    assert not ctx.registry.issues
    assert not [record for record in caplog.records if record.levelno >= logging.ERROR]
    for private in (MCP_URL, "test-private-detail", "test-ha-token"):
        assert private not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [CoreState.starting, CoreState.running])
@pytest.mark.parametrize(
    ("failure", "reason"),
    [
        (404, "MCP endpoint returned HTTP 404"),
        (503, "MCP endpoint returned HTTP 503"),
        (TimeoutError("test-private-detail"), "MCP endpoint timed out"),
        (URLError("test-private-detail"), "Could not connect to the MCP endpoint"),
    ],
)
async def test_unavailable_endpoint_retries_with_repair_only_after_startup(
    setup_context, monkeypatch, caplog, state, failure, reason
):
    ctx = setup_context
    ctx.hass.set_state(state)
    caplog.set_level(logging.DEBUG, logger=integration.__name__)
    error = http_error(failure) if isinstance(failure, int) else failure
    monkeypatch.setattr(mcp_url, "urlopen", Mock(side_effect=error))

    await ctx.entry.async_setup_locked(ctx.hass)

    assert ctx.entry.state is ConfigEntryState.SETUP_RETRY
    assert ctx.entry.reason == reason
    assert ctx.entry.entry_id not in ctx.hass.data[DOMAIN]
    assert ctx.entry._async_cancel_retry_setup is not None
    ctx.tunnel.start.assert_not_awaited()
    ctx.updater.async_start_auto_update.assert_not_awaited()
    issue = ctx.registry.async_get_issue(DOMAIN, "mcp_url_unreachable")
    if state is CoreState.running:
        assert issue is not None
        assert issue.translation_placeholders == {
            "url": "http://127.0.0.1:8123/**REDACTED**",
            "reason": reason,
        }
    else:
        assert issue is None
    assert ctx.registry.async_get_issue(DOMAIN, "startup_failed") is None
    assert not [record for record in caplog.records if record.levelno >= logging.ERROR]
    for private in (MCP_URL, "test-private-detail", "test-ha-token"):
        assert private not in caplog.text


@pytest.mark.asyncio
async def test_persistent_failure_recovers_and_clears_repairs(
    setup_context, monkeypatch
):
    ctx = setup_context
    monkeypatch.setattr(
        mcp_url,
        "urlopen",
        Mock(side_effect=[http_error(404), http_error(404), http_error(405)]),
    )
    await create_issue(ctx.hass, "startup_failed", "startup_failed", {"error": "test"})

    await ctx.entry.async_setup_locked(ctx.hass)
    assert ctx.entry.state is ConfigEntryState.SETUP_RETRY
    ctx.hass.set_state(CoreState.running)
    ctx.hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await ctx.hass.async_block_till_done(wait_background_tasks=True)
    assert ctx.entry.state is ConfigEntryState.SETUP_RETRY
    assert ctx.registry.async_get_issue(DOMAIN, "mcp_url_unreachable") is not None
    ctx.tunnel.start.assert_not_awaited()

    # Retry immediately instead of waiting through HA's exponential backoff.
    ctx.entry.async_cancel_retry_setup()
    await ctx.entry.async_setup_locked(ctx.hass)

    assert ctx.entry.state is ConfigEntryState.LOADED
    assert not ctx.registry.issues
    ctx.tunnel.start.assert_awaited_once()


@pytest.mark.asyncio
async def test_invalid_url_still_creates_repair_during_startup(
    setup_context, monkeypatch
):
    ctx = setup_context
    # ConfigEntry data is immutable; model an invalid persisted configuration.
    object.__setattr__(ctx.entry, "data", {CONF_HA_MCP_URL: "not-a-url"})
    probe = Mock()
    monkeypatch.setattr(mcp_url, "urlopen", probe)

    await ctx.entry.async_setup_locked(ctx.hass)

    assert ctx.entry.state is ConfigEntryState.SETUP_RETRY
    assert ctx.entry.reason == "invalid_mcp_url"
    assert ctx.registry.async_get_issue(DOMAIN, "mcp_url_unreachable") is not None
    assert ctx.entry.entry_id not in ctx.hass.data[DOMAIN]
    probe.assert_not_called()
    ctx.tunnel.start.assert_not_awaited()


@pytest.mark.asyncio
async def test_unexpected_start_failure_is_still_logged(
    setup_context, monkeypatch, caplog
):
    ctx = setup_context
    monkeypatch.setattr(mcp_url, "urlopen", Mock(side_effect=http_error(405)))
    ctx.tunnel.start.side_effect = RuntimeError("test unexpected failure")

    await ctx.entry.async_setup_locked(ctx.hass)

    assert ctx.registry.async_get_issue(DOMAIN, "startup_failed") is not None
    assert "Failed to start OpenAI Tunnel for HA-MCP" in caplog.text
    assert "test unexpected failure" in caplog.text
