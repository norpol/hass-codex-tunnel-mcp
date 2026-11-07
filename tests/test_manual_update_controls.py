"""Entry-local controls and explicit manual release policy."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.hass_codex_tunnel_mcp import updater as module
from custom_components.hass_codex_tunnel_mcp.binary import (
    ChecksumError,
    TunnelClientError,
    parse_github_releases,
)
from custom_components.hass_codex_tunnel_mcp.button import TunnelUpdateButton
from custom_components.hass_codex_tunnel_mcp.const import DOMAIN
from custom_components.hass_codex_tunnel_mcp.control import LATEST_ELIGIBLE
from custom_components.hass_codex_tunnel_mcp.select import TunnelReleaseSelect
from custom_components.hass_codex_tunnel_mcp.updater import (
    TunnelClientUpdater,
    UpdateCheckResult,
)

PUBLISHED = datetime(2026, 11, 6, 22, 5, 17, tzinfo=UTC)


def release(version="v0.0.20", **changes):
    result = {
        "tag_name": version,
        "published_at": PUBLISHED.isoformat(),
        "draft": False,
        "prerelease": False,
        "assets": [
            {
                "name": f"tunnel-client-{version}-linux-amd64.zip",
                "digest": "sha256:" + "a" * 64,
                "browser_download_url": f"https://github.com/openai/tunnel-client/releases/download/{version}/tunnel-client-{version}-linux-amd64.zip",
            }
        ],
    }
    result.update(changes)
    return result


@pytest.fixture
def setup(tmp_path, monkeypatch):
    updater = TunnelClientUpdater(Mock(), Mock(data={}, options={}), tmp_path, Mock())
    updater._lock = asyncio.Lock()
    updater.state.active_version = "v0.0.19"
    monkeypatch.setattr(
        module,
        "fetch_github_releases",
        AsyncMock(return_value=parse_github_releases([release()])),
    )
    monkeypatch.setattr(module, "create_issue", AsyncMock())
    monkeypatch.setattr(module, "delete_issue", AsyncMock())
    # Platform choice is explicit here; real-binary tests run on both CI arches.
    from custom_components.hass_codex_tunnel_mcp import binary

    monkeypatch.setattr(binary.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(binary.platform, "system", lambda: "Linux")
    install = AsyncMock(return_value=tmp_path / "v0.0.20" / "tunnel-client")
    monkeypatch.setattr(module, "ensure_tunnel_client", install)
    tunnel = Mock(start=AsyncMock(), wait_until_healthy=AsyncMock(return_value=True))
    runtime = {
        "updater": updater,
        "tunnel": tunnel,
        "listeners": [],
        "entry_data": lambda: {"tunnel_id": "first"},
    }
    entry = SimpleNamespace(entry_id="first", title="First tunnel")
    hass = SimpleNamespace(data={DOMAIN: {"first": runtime}})
    return updater, install, tunnel, runtime, entry, hass


@pytest.mark.asyncio
@pytest.mark.parametrize("offset,eligible", [(-1, False), (0, True), (1, True)])
async def test_v020_exact_hold_boundary(setup, offset, eligible):
    updater, install, *_ = setup
    deadline = PUBLISHED + timedelta(days=8)
    result = await updater.async_check_for_update(
        now=deadline + timedelta(microseconds=offset)
    )
    assert (result.candidate_version == "v0.0.20") is eligible
    assert result.deferred_until == (None if eligible else deadline.isoformat())
    assert module.UPDATE_BACKOFF_DAYS == 8
    install.assert_not_awaited()


@pytest.mark.asyncio
async def test_selection_does_not_change_automatic_or_default_manual_updates(setup):
    updater, install, tunnel, *_ = setup
    updater.selected_version = "v0.0.20"
    for automatic in (True, False):
        result = await updater.async_update_tunnel_client(
            tunnel, {}, now=PUBLISHED, automatic=automatic
        )
        assert result.candidate_version is None
    install.assert_not_awaited()
    tunnel.start.assert_not_awaited()
    assert updater.selected_version == "v0.0.20"


@pytest.mark.asyncio
async def test_automation_cannot_request_age_override(setup):
    updater, install, tunnel, *_ = setup
    with pytest.raises(TunnelClientError, match="automatic updates cannot"):
        await updater.async_update_tunnel_client(
            tunnel, {}, now=PUBLISHED, automatic=True, selected_version="v0.0.20"
        )
    install.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("ready", [True, False])
async def test_manual_early_update_uses_readiness_and_rollback(setup, ready):
    updater, install, tunnel, *_ = setup
    tunnel.wait_until_healthy = AsyncMock(side_effect=[ready, True])
    if ready:
        await updater.async_update_tunnel_client(
            tunnel, {}, now=PUBLISHED, selected_version="v0.0.20"
        )
        assert updater.state.active_version == "v0.0.20"
        assert updater.state.previous_version == "v0.0.19"
        assert updater.state.deferred_until is None
    else:
        with pytest.raises(TunnelClientError, match="did not become ready"):
            await updater.async_update_tunnel_client(
                tunnel, {}, now=PUBLISHED, selected_version="v0.0.20"
            )
        assert updater.state.active_version == "v0.0.19"
        assert "v0.0.20" in updater.state.failed_versions
        tunnel.start.assert_awaited_with({})
    install.assert_awaited_once()


@pytest.mark.asyncio
async def test_manual_override_never_bypasses_checksum(setup):
    updater, install, tunnel, *_ = setup
    install.side_effect = ChecksumError("fixture mismatch")
    with pytest.raises(ChecksumError):
        await updater.async_update_tunnel_client(
            tunnel, {}, now=PUBLISHED, selected_version="v0.0.20"
        )
    tunnel.start.assert_not_awaited()
    assert updater.state.active_version == "v0.0.19"
    assert "v0.0.20" in updater.state.failed_versions


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_release",
    [
        release(draft=True),
        release(prerelease=True),
        release("v0.0.20-rc.1"),
        release("v0.0.18"),
        release("v0.0.19"),
        release(published_at=(PUBLISHED + timedelta(days=1)).isoformat()),
    ],
)
async def test_manual_rejects_unpublished_nonstable_and_nonupgrades(
    setup, monkeypatch, bad_release
):
    updater, install, tunnel, *_ = setup
    monkeypatch.setattr(
        module,
        "fetch_github_releases",
        AsyncMock(return_value=parse_github_releases([bad_release])),
    )
    with pytest.raises(TunnelClientError, match="published newer stable"):
        await updater.async_update_tunnel_client(
            tunnel, {}, now=PUBLISHED, selected_version=bad_release["tag_name"]
        )
    install.assert_not_awaited()
    tunnel.start.assert_not_awaited()


@pytest.mark.asyncio
async def test_entry_controls_select_then_install_once_and_reset_after_failure(setup):
    updater, _, tunnel, runtime, entry, hass = setup
    unrelated = Mock()
    hass.data[DOMAIN]["second"] = {"updater": unrelated}
    check = TunnelUpdateButton(hass, entry, "check_releases")
    select = TunnelReleaseSelect(hass, entry)
    install_button = TunnelUpdateButton(hass, entry, "install_release")
    assert select.options == [LATEST_ELIGIBLE]
    assert select.current_option == LATEST_ELIGIBLE
    await check.async_press()
    assert select.options == [LATEST_ELIGIBLE, "v0.0.20"]
    updater.async_update_tunnel_client = AsyncMock(
        side_effect=TunnelClientError("fixture failure")
    )
    await select.async_select_option("v0.0.20")
    with pytest.raises(HomeAssistantError, match="fixture failure"):
        await install_button.async_press()
    updater.async_update_tunnel_client.assert_awaited_once_with(
        tunnel, {"tunnel_id": "first"}, selected_version="v0.0.20"
    )
    assert select.current_option == LATEST_ELIGIBLE
    assert not unrelated.mock_calls
    with pytest.raises(HomeAssistantError):
        await select.async_select_option("v99.0.0")


@pytest.mark.asyncio
async def test_default_button_keeps_hold_and_reports_deferral(setup):
    updater, _, tunnel, runtime, entry, hass = setup
    deadline = (PUBLISHED + timedelta(days=8)).isoformat()
    updater.async_update_tunnel_client = AsyncMock(
        return_value=UpdateCheckResult("v0.0.20", "v0.0.19", None, deadline)
    )
    with pytest.raises(HomeAssistantError, match="next release eligibility"):
        await TunnelUpdateButton(hass, entry, "install_release").async_press()
    updater.async_update_tunnel_client.assert_awaited_once_with(
        tunnel, {"tunnel_id": "first"}, selected_version=None
    )


@pytest.mark.asyncio
async def test_controls_register_with_home_assistant_and_remove_listeners(
    setup, tmp_path
):
    """Use real HA entity platforms and state publication, without a tunnel."""
    import logging
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_component import EntityComponent
    from homeassistant.helpers import entity_registry, device_registry

    updater, _, _, runtime, entry, _ = setup
    hass = HomeAssistant(str(tmp_path))
    hass.data[DOMAIN] = {entry.entry_id: runtime}
    device_registry.async_setup(hass)
    await device_registry.async_load(hass, load_empty=True)
    await entity_registry.async_load(hass, load_empty=True)
    select = TunnelReleaseSelect(hass, entry)
    button = TunnelUpdateButton(hass, entry, "check_releases")
    assert select.device_info["identifiers"] == {(DOMAIN, entry.entry_id)}
    assert button.device_info == select.device_info
    # Device registration/translation belongs to the config-entry loader;
    # this standalone platform tests entity state and listener lifecycle.
    select._attr_device_info = button._attr_device_info = None
    select._attr_name = "Release override"
    button._attr_name = "Check releases"
    select.entity_id = "select.tunnel_release_override"
    button.entity_id = "button.tunnel_check_releases"
    # EntityComponent supplies the real HA add/remove lifecycle. The custom
    # platform loader supplies translated names in deployment; IDs are fixed
    # here so the test concerns state, behavior, and callback cleanup.
    selects = EntityComponent(logging.getLogger(__name__), "select", hass)
    buttons = EntityComponent(logging.getLogger(__name__), "button", hass)
    try:
        await selects.async_add_entities([select])
        await buttons.async_add_entities([button])
        assert len(runtime["listeners"]) == 2
        updater._notify = lambda: [listener() for listener in runtime["listeners"]]
        await button.async_press()
        await select.async_select_option("v0.0.20")
        assert hass.states.get(select.entity_id).state == "v0.0.20"
        assert hass.states.get(select.entity_id).attributes["options"] == [
            LATEST_ELIGIBLE,
            "v0.0.20",
        ]
        await select.async_select_option(LATEST_ELIGIBLE)
        assert hass.states.get(select.entity_id).state == LATEST_ELIGIBLE
    finally:
        await select.async_remove()
        await button.async_remove()
        await hass.async_stop()
    assert runtime["listeners"] == []
