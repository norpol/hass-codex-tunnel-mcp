from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.hass_codex_tunnel_mcp import updater as updater_module
from custom_components.hass_codex_tunnel_mcp.binary import parse_github_releases
from custom_components.hass_codex_tunnel_mcp.updater import (
    TunnelClientUpdater,
    TunnelClientUpdaterState,
)


def test_updater_state_round_trips_status_fields() -> None:
    state = TunnelClientUpdaterState.from_dict(
        {
            "active_version": "v0.0.11",
            "previous_version": "v0.0.10",
            "latest_seen_version": "v0.0.12",
            "latest_eligible_version": "v0.0.11",
            "deferred_until": "2026-07-20T00:00:00+00:00",
            "last_check": "2026-07-12T00:00:00+00:00",
            "last_update": "2026-07-12T01:00:00+00:00",
            "last_error": "candidate failed",
            "failed_versions": {"v0.0.12": "not ready"},
            "active_asset": {
                "version": "v0.0.11",
                "os_name": "linux",
                "arch": "amd64",
                "filename": "tunnel-client-v0.0.11-linux-amd64.zip",
                "sha256": "a" * 64,
            },
        }
    )

    assert state.to_dict()["active_version"] == "v0.0.11"
    assert state.to_dict()["failed_versions"] == {"v0.0.12": "not ready"}
    assert state.as_attributes()["active_version"] == "v0.0.11"
    assert state.as_attributes()["failed_update_versions"] == ["v0.0.12"]


@pytest.mark.asyncio
@pytest.mark.parametrize("cached_binary", ["executable", "missing", "not_executable"])
async def test_rollback_to_former_pinned_version_without_asset_metadata(
    tmp_path, monkeypatch, cached_binary
):
    updater = TunnelClientUpdater(Mock(), Mock(), tmp_path, Mock())
    updater.state = TunnelClientUpdaterState(
        active_version="v0.0.16", previous_version="v0.0.10"
    )
    executable = tmp_path / "v0.0.10" / "tunnel-client"
    if cached_binary != "missing":
        executable.parent.mkdir()
        executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        executable.chmod(0o755 if cached_binary == "executable" else 0o644)
    install = AsyncMock()
    create_issue, delete_issue = AsyncMock(), AsyncMock()
    monkeypatch.setattr(updater_module, "ensure_tunnel_client", install)
    monkeypatch.setattr(updater_module, "create_issue", create_issue)
    monkeypatch.setattr(updater_module, "delete_issue", delete_issue)
    tunnel = Mock(start=AsyncMock(), wait_until_healthy=AsyncMock(return_value=True))
    entry_data = {"tunnel_id": "existing-tunnel"}

    result = await updater.async_rollback(tunnel, entry_data)

    install.assert_not_awaited()
    if cached_binary == "executable":
        assert result is True
        tunnel.start.assert_awaited_once_with(
            entry_data, executable_override=executable
        )
        tunnel.wait_until_healthy.assert_awaited_once()
        assert updater.state.active_version == "v0.0.10"
        assert updater.state.previous_version == "v0.0.16"
        assert updater.state.last_error is None
        create_issue.assert_not_awaited()
    else:
        assert result is False
        tunnel.start.assert_not_awaited()
        assert updater.state.active_version == "v0.0.16"
        assert updater.state.previous_version == "v0.0.10"
        assert updater.state.last_error is not None
        create_issue.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("active,deferred", [("v0.0.16", True), ("v0.0.18", False), ("v0.0.19", False)])
async def test_only_newer_uninstalled_releases_are_deferred(tmp_path, monkeypatch, active, deferred):
    updater = TunnelClientUpdater(Mock(), Mock(), tmp_path, Mock())
    updater.state.active_version = active
    releases = parse_github_releases([{
        "tag_name": "v0.0.18", "published_at": "2026-10-23T20:25:58Z",
        "draft": False, "prerelease": False, "assets": [],
    }])
    monkeypatch.setattr(updater_module, "fetch_github_releases", AsyncMock(return_value=releases))
    monkeypatch.setattr(updater_module, "delete_issue", AsyncMock())
    result = await updater.async_check_for_update(now=datetime(2026, 10, 25, tzinfo=UTC))
    assert bool(result.deferred_until) is deferred
    assert result.candidate_asset is None
