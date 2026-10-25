"""Exercise automatic updates through Home Assistant's real interval helper."""

from __future__ import annotations

import asyncio
import threading
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from unittest.mock import AsyncMock, Mock

import pytest
import pytest_asyncio
from freezegun import freeze_time
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import frame
from homeassistant.helpers.storage import Store
from homeassistant.loader import DATA_INTEGRATIONS
from homeassistant.util import dt as dt_util

from custom_components.hass_codex_tunnel_mcp import updater as updater_module
from custom_components.hass_codex_tunnel_mcp.binary import (
    ChecksumError,
    TunnelClientAsset,
    parse_github_releases,
)
from custom_components.hass_codex_tunnel_mcp.const import (
    CONF_AUTO_UPDATE_TUNNEL_CLIENT,
    DOMAIN,
)
from custom_components.hass_codex_tunnel_mcp.updater import (
    UPDATE_CHECK_INTERVAL,
    TunnelClientUpdater,
    UpdateCheckResult,
)

START = datetime(2026, 10, 17, tzinfo=UTC)


@pytest_asyncio.fixture
async def scheduler(tmp_path, monkeypatch):
    """Use real HA job dispatch and timers, advancing time without sleeping."""
    with freeze_time(START) as clock:
        hass = HomeAssistant(str(tmp_path))
        hass.data[DATA_INTEGRATIONS] = {}
        monkeypatch.setattr(hass.loop, "_debug", True)
        # Small monotonic values retain asyncio's subsecond timer resolution.
        monkeypatch.setattr(
            hass.loop,
            "time",
            lambda: (clock() - START.replace(tzinfo=None)).total_seconds(),
        )
        monkeypatch.setattr(hass.loop, "slow_callback_duration", float("inf"))
        monkeypatch.setattr(
            dt_util, "utcnow", lambda: clock().replace(tzinfo=UTC)
        )
        monkeypatch.setattr(frame._hass, "hass", hass)
        monkeypatch.setattr(Store, "async_load", AsyncMock(return_value=None))
        monkeypatch.setattr(Store, "async_save", AsyncMock())
        entries = []

        async def make_updater(entry_id="first", *, enabled=True):
            entry = ConfigEntry(
                entry_id=entry_id,
                domain=DOMAIN,
                title=entry_id,
                data={"tunnel_id": entry_id},
                options={CONF_AUTO_UPDATE_TUNNEL_CLIENT: enabled},
                version=1,
                minor_version=1,
                source="user",
                unique_id=entry_id,
                discovery_keys=MappingProxyType({}),
                subentries_data=None,
            )
            entries.append(entry)
            updater = TunnelClientUpdater(hass, entry, tmp_path / "bin", Mock())
            await updater.async_load()
            return updater

        async def advance(delta=UPDATE_CHECK_INTERVAL, *, wait=True):
            clock.tick(delta)
            # Let asyncio dispatch due timers before draining their HA jobs.
            await asyncio.sleep(0)
            if wait:
                await hass.async_block_till_done(wait_background_tasks=True)

        yield hass, make_updater, advance

        for entry in entries:
            await entry._async_process_on_unload(hass)
        await hass.async_block_till_done(wait_background_tasks=True)
        hass.import_executor.shutdown(wait=True)


@pytest.mark.asyncio
async def test_periodic_checks_run_on_event_loop(scheduler, monkeypatch, caplog):
    """Repeated daily ticks must reach the real check path, including last_check."""
    hass, make_updater, advance = scheduler
    updater = await make_updater()
    loop_thread = threading.get_ident()
    fetch_threads = []

    async def fetch_releases():
        assert asyncio.get_running_loop() is hass.loop
        fetch_threads.append(threading.get_ident())
        return []

    fetch = AsyncMock(side_effect=fetch_releases)
    monkeypatch.setattr(updater_module, "fetch_github_releases", fetch)
    monkeypatch.setattr(updater_module, "delete_issue", AsyncMock())
    tunnel = Mock()
    await updater.async_start_auto_update(tunnel, lambda: updater.entry.data)

    await advance(UPDATE_CHECK_INTERVAL - timedelta(seconds=1))
    fetch.assert_not_awaited()
    assert updater.state.last_check is None

    await advance(timedelta(seconds=1))
    assert updater.state.last_check == (START + UPDATE_CHECK_INTERVAL).isoformat()
    for day in range(2, 6):
        await advance()
        assert (
            updater.state.last_check
            == (START + day * UPDATE_CHECK_INTERVAL).isoformat()
        )

    assert fetch.await_count == 5
    assert fetch_threads == [loop_thread] * 5
    tunnel.start.assert_not_called()
    assert "thread other than the event loop" not in caplog.text


@pytest.mark.asyncio
async def test_entries_have_independent_options_and_unload(scheduler, monkeypatch):
    hass, make_updater, advance = scheduler
    first = await make_updater("first")
    second = await make_updater("second", enabled=False)
    first_update = AsyncMock()
    second_update = AsyncMock()
    monkeypatch.setattr(first, "async_update_tunnel_client", first_update)
    monkeypatch.setattr(second, "async_update_tunnel_client", second_update)
    first_tunnel, second_tunnel = Mock(), Mock()
    first_data = Mock(return_value={"tunnel_id": "first"})
    second_data = Mock(return_value={"tunnel_id": "second"})
    await first.async_start_auto_update(first_tunnel, first_data)
    await second.async_start_auto_update(second_tunnel, second_data)

    await advance()
    first_update.assert_awaited_once_with(
        first_tunnel,
        first_data.return_value,
        now=START + UPDATE_CHECK_INTERVAL,
        automatic=True,
    )
    second_update.assert_not_awaited()
    second_data.assert_not_called()

    # Options and connection data must be evaluated at each tick, not captured.
    object.__setattr__(second.entry, "options", {CONF_AUTO_UPDATE_TUNNEL_CLIENT: True})
    second_data.return_value = {"tunnel_id": "second", "updated": True}
    await advance()
    assert first_update.await_count == 2
    second_update.assert_awaited_once_with(
        second_tunnel,
        second_data.return_value,
        now=START + 2 * UPDATE_CHECK_INTERVAL,
        automatic=True,
    )

    await first.entry._async_process_on_unload(hass)
    await advance()
    assert first_update.await_count == 2
    assert second_update.await_count == 2
    await second.entry._async_process_on_unload(hass)
    await advance()
    assert second_update.await_count == 2


@pytest.mark.asyncio
async def test_failed_tick_does_not_stop_future_checks_or_other_entries(
    scheduler, monkeypatch, caplog
):
    _, make_updater, advance = scheduler
    first = await make_updater("first")
    second = await make_updater("second")
    first_update = AsyncMock(side_effect=[RuntimeError("temporary failure"), None])
    second_update = AsyncMock()
    monkeypatch.setattr(first, "async_update_tunnel_client", first_update)
    monkeypatch.setattr(second, "async_update_tunnel_client", second_update)
    await first.async_start_auto_update(Mock(), lambda: first.entry.data)
    await second.async_start_auto_update(Mock(), lambda: second.entry.data)

    await advance()
    assert first_update.await_count == second_update.await_count == 1
    assert "Automatic tunnel-client update failed: temporary failure" in caplog.text
    await advance()
    assert first_update.await_count == second_update.await_count == 2


@pytest.mark.asyncio
async def test_entries_serialize_installation_in_shared_binary_directory(
    scheduler, monkeypatch, tmp_path
):
    """One entry's failed candidate must not race another entry's installation."""
    hass, make_updater, advance = scheduler
    first = await make_updater("first")
    second = await make_updater("second")
    asset = TunnelClientAsset("linux", "amd64", "candidate.zip", "a" * 64, "v0.0.11")
    result = UpdateCheckResult("v0.0.11", "v0.0.11", asset, None)
    for updater in (first, second):
        monkeypatch.setattr(
            updater, "async_check_for_update", AsyncMock(return_value=result)
        )
    monkeypatch.setattr(updater_module, "create_issue", AsyncMock())
    monkeypatch.setattr(updater_module, "delete_issue", AsyncMock())
    entered = asyncio.Event()
    release = asyncio.Event()
    active_installs = 0
    max_active_installs = 0

    async def install(*args, **kwargs):
        nonlocal active_installs, max_active_installs
        active_installs += 1
        max_active_installs = max(max_active_installs, active_installs)
        entered.set()
        try:
            await release.wait()
            return tmp_path / "bin" / "v0.0.11" / "tunnel-client"
        finally:
            active_installs -= 1

    installer = AsyncMock(side_effect=install)
    monkeypatch.setattr(updater_module, "ensure_tunnel_client", installer)
    first_tunnel = Mock(
        start=AsyncMock(), wait_until_healthy=AsyncMock(side_effect=[False, True])
    )
    second_tunnel = Mock(
        start=AsyncMock(), wait_until_healthy=AsyncMock(return_value=True)
    )
    await first.async_start_auto_update(first_tunnel, lambda: first.entry.data)
    await second.async_start_auto_update(second_tunnel, lambda: second.entry.data)

    await advance(wait=False)
    await entered.wait()
    await asyncio.sleep(0)
    release.set()
    await hass.async_block_till_done(wait_background_tasks=True)

    assert max_active_installs == 1
    assert installer.await_count == 2
    assert first.state.active_version == "v0.0.10"
    assert "v0.0.11" in first.state.failed_versions
    assert second.state.active_version == "v0.0.11"
    assert second.state.failed_versions == {}
    first_tunnel.start.assert_awaited_with(first.entry.data)
    second_tunnel.start.assert_awaited_once_with(
        second.entry.data,
        executable_override=tmp_path / "bin" / "v0.0.11" / "tunnel-client",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["ready", "bad_checksum", "not_ready"])
async def test_scheduled_updates_preserve_eligibility_and_rollback(
    scheduler, monkeypatch, tmp_path, outcome
):
    """The scheduler uses the existing verified, health-gated update pipeline."""
    _, make_updater, advance = scheduler
    updater = await make_updater()
    releases = parse_github_releases(
        [
            {
                "tag_name": "v0.0.11",
                "published_at": (START - timedelta(days=6)).isoformat(),
                "draft": False,
                "prerelease": False,
                "assets": [
                    {
                        "name": f"tunnel-client-v0.0.11-linux-{arch}.zip",
                        "digest": "sha256:" + "a" * 64,
                        "browser_download_url": f"https://example.com/{arch}.zip",
                    }
                    for arch in ("amd64", "arm64")
                ],
            }
        ]
    )
    fetch = AsyncMock(return_value=releases)
    monkeypatch.setattr(updater_module, "fetch_github_releases", fetch)
    monkeypatch.setattr(updater_module, "create_issue", AsyncMock())
    monkeypatch.setattr(updater_module, "delete_issue", AsyncMock())
    executable = tmp_path / "candidate" / "tunnel-client"
    install = AsyncMock(return_value=executable)
    if outcome == "bad_checksum":
        install.side_effect = ChecksumError("checksum mismatch")
    monkeypatch.setattr(updater_module, "ensure_tunnel_client", install)
    tunnel = Mock(
        start=AsyncMock(),
        wait_until_healthy=AsyncMock(
            side_effect=[False, True] if outcome == "not_ready" else [True]
        ),
    )
    await updater.async_start_auto_update(tunnel, lambda: updater.entry.data)

    # At seven days the release is visible but must not be installed.
    await advance()
    assert updater.state.latest_seen_version == "v0.0.11"
    assert updater.state.latest_eligible_version is None
    assert (
        updater.state.deferred_until == (START + 2 * UPDATE_CHECK_INTERVAL).isoformat()
    )
    install.assert_not_awaited()
    tunnel.start.assert_not_awaited()

    # At exactly eight days it becomes eligible through the normal check path.
    await advance()
    assert updater.state.latest_eligible_version == "v0.0.11"
    assert updater.state.deferred_until is None
    install.assert_awaited_once()
    assert install.await_args.args == (updater.install_root,)
    assert install.await_args.kwargs["asset"].sha256 == "a" * 64
    if outcome == "ready":
        tunnel.start.assert_awaited_once_with(
            updater.entry.data, executable_override=executable
        )
        assert updater.state.active_version == "v0.0.11"
        assert updater.state.previous_version == "v0.0.10"
        assert (
            updater.state.last_update == (START + 2 * UPDATE_CHECK_INTERVAL).isoformat()
        )
        assert updater.state.failed_versions == {}
    else:
        assert updater.state.active_version == "v0.0.10"
        assert updater.state.last_update is None
        assert "v0.0.11" in updater.state.failed_versions
        if outcome == "bad_checksum":
            tunnel.start.assert_not_awaited()
        else:
            assert tunnel.start.await_count == 2
            assert tunnel.start.await_args_list[0].kwargs == {
                "executable_override": executable
            }
            tunnel.start.assert_awaited_with(updater.entry.data)
            assert tunnel.wait_until_healthy.await_count == 2

    # A failed candidate is skipped automatically; a successful one is current.
    await advance()
    assert fetch.await_count == 3
    install.assert_awaited_once()
