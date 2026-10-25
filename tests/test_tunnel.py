from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.hass_codex_tunnel_mcp.const import (
    CONF_API_KEY,
    CONF_CONTROL_PLANE_BASE_URL,
    CONF_CONTROL_PLANE_PATH,
    CONF_HA_MCP_BEARER_TOKEN,
    CONF_HA_MCP_URL,
    CONF_TUNNEL_ID,
)
from custom_components.hass_codex_tunnel_mcp.tunnel import (
    TunnelCommandConfig,
    TunnelManager,
    _read_health_url,
    build_mcp_server_url,
    build_tunnel_command,
)


def test_build_mcp_server_url() -> None:
    assert (
        build_mcp_server_url("http://127.0.0.1:9584/private_secret")
        == "channel=main,url=http://127.0.0.1:9584/private_secret"
    )


def test_build_tunnel_command_includes_required_flags(tmp_path: Path) -> None:
    command = build_tunnel_command(
        tmp_path / "tunnel-client",
        TunnelCommandConfig(
            tunnel_id="tunnel_0123456789abcdef0123456789abcdef",
            mcp_server_url="http://127.0.0.1:9584/private_secret",
            run_dir=tmp_path,
            control_plane_base_url="https://control.example",
            control_plane_url_path="/v1",
        ),
    )

    assert command[:2] == [str(tmp_path / "tunnel-client"), "run"]
    assert "--control-plane.tunnel-id" in command
    assert "tunnel_0123456789abcdef0123456789abcdef" in command
    assert "--control-plane.api-key" in command
    assert "env:CONTROL_PLANE_API_KEY" in command
    assert "--mcp.server-url" in command
    assert "channel=main,url=http://127.0.0.1:9584/private_secret" in command
    assert "--health.listen-addr" in command
    assert "127.0.0.1:0" in command
    assert "--control-plane.base-url" in command
    assert "https://control.example" in command
    assert "--control-plane.url-path" in command
    assert "/v1" in command


def test_build_tunnel_command_uses_env_backed_ha_token_header(tmp_path: Path) -> None:
    command = build_tunnel_command(
        tmp_path / "tunnel-client",
        TunnelCommandConfig(
            tunnel_id="tunnel_0123456789abcdef0123456789abcdef",
            mcp_server_url="http://127.0.0.1:8123/api/mcp",
            run_dir=tmp_path,
            use_ha_mcp_bearer_token=True,
        ),
    )

    assert "--mcp.extra-headers" in command
    assert "--mcp.discovery-extra-headers" in command
    assert command.count("Authorization: env:HA_MCP_AUTH_HEADER") == 2
    assert not any("Bearer" in arg for arg in command)


def test_tunnel_manager_lifecycle_with_fake_client(tmp_path: Path) -> None:
    asyncio.run(_run_tunnel_manager_lifecycle_with_fake_client(tmp_path))


def test_tunnel_manager_cleans_up_stale_client(tmp_path: Path) -> None:
    asyncio.run(_run_tunnel_manager_cleans_up_stale_client(tmp_path))


def test_read_health_url(tmp_path: Path) -> None:
    health_file = tmp_path / "health.url"

    assert _read_health_url(health_file) == ""

    health_file.write_text("http://127.0.0.1:9\n", encoding="utf-8")

    assert _read_health_url(health_file) == "http://127.0.0.1:9"


@pytest.mark.asyncio
@pytest.mark.parametrize("cancellations", [0, 1, 2])
async def test_stop_finishes_cleanup_despite_cancellation(
    tmp_path, monkeypatch, cancellations
):
    """Concurrent callers must await one shutdown, even if one is cancelled."""
    loop = asyncio.get_running_loop()
    now = 0.0
    monkeypatch.setattr(loop, "time", lambda: now)
    waiting = asyncio.Event()
    killed = asyncio.Event()
    reaped = asyncio.Event()

    async def wait_for_exit():
        waiting.set()
        await reaped.wait()
        process.returncode = -9
        return process.returncode

    process = Mock(
        returncode=None,
        wait=AsyncMock(side_effect=wait_for_exit),
        kill=Mock(side_effect=killed.set),
    )
    notify = Mock()
    manager = TunnelManager(lambda force: None, tmp_path, notify)
    manager._process = process
    manager.status.state = "running"
    watch = manager._watch_task = asyncio.create_task(asyncio.Event().wait())
    logs = [asyncio.create_task(asyncio.Event().wait()) for _ in range(2)]
    manager._log_tasks = logs.copy()
    first = asyncio.create_task(manager.stop())
    callers = [first]
    try:
        await waiting.wait()
        assert manager.process is process
        if cancellations:
            first.cancel("cancel update")
            await asyncio.sleep(0)
        second = asyncio.create_task(manager.stop())
        callers.append(second)
        await asyncio.sleep(0)

        # Exercise the real timeout without waiting ten seconds in the test.
        now = 10.0
        await killed.wait()
        if cancellations == 2:
            first.cancel("cancel update again")
            await asyncio.sleep(0)
        assert not first.done()
        assert not second.done()
        assert manager.process is process
        assert manager.status.state == "running"
        process.terminate.assert_called_once_with()
        process.kill.assert_called_once_with()
        notify.assert_not_called()
    finally:
        reaped.set()
        results = await asyncio.gather(*callers, return_exceptions=True)

    if cancellations:
        assert isinstance(results[0], asyncio.CancelledError)
    else:
        assert results[0] is None
    assert results[1] is None
    assert process.returncode == -9
    assert process.wait.await_count == 2
    assert manager.process is None
    assert watch.cancelled()
    assert all(task.cancelled() for task in logs)
    assert manager._watch_task is None
    assert manager._log_tasks == []
    assert manager.status.state == "stopped"
    notify.assert_called_once_with()


async def _run_tunnel_manager_lifecycle_with_fake_client(tmp_path: Path) -> None:
    fake_client = tmp_path / "tunnel-client"
    fake_client.write_text(
        "#!/usr/bin/env python3\n"
        "import http.server, pathlib, sys\n"
        "class Handler(http.server.BaseHTTPRequestHandler):\n"
        "    def do_GET(self):\n"
        "        self.send_response(200 if self.path == '/readyz' else 404)\n"
        "        self.end_headers()\n"
        "server = http.server.HTTPServer(('127.0.0.1', 0), Handler)\n"
        "health_file = pathlib.Path(sys.argv[sys.argv.index('--health.url-file') + 1])\n"
        "health_file.write_text(f'http://127.0.0.1:{server.server_port}/health')\n"
        "server.serve_forever()\n",
        encoding="utf-8",
    )
    fake_client.chmod(0o755)
    notifications = 0

    def notify() -> None:
        nonlocal notifications
        notifications += 1

    manager = TunnelManager(lambda force: fake_client, tmp_path / "run", notify)
    await manager.start(
        {
            CONF_TUNNEL_ID: "tunnel_0123456789abcdef0123456789abcdef",
            CONF_API_KEY: "runtime-key",
            CONF_HA_MCP_URL: "http://127.0.0.1:9584/private_secret",
            CONF_HA_MCP_BEARER_TOKEN: "",
            CONF_CONTROL_PLANE_BASE_URL: "",
            CONF_CONTROL_PLANE_PATH: "",
        }
    )
    for _ in range(20):
        if manager.status.healthy:
            break
        await asyncio.sleep(0.1)

    assert manager.status.healthy is True
    assert manager.status.health_url == (tmp_path / "run/health.url").read_text()
    assert notifications > 0

    await manager.stop()
    assert manager.status.state == "stopped"


@pytest.mark.asyncio
async def test_readiness_tracks_http_probe_not_health_file(tmp_path):
    """A published URL and liveness 200 must not mask readiness 503 or failure."""
    from aiohttp import web

    response_status = 503

    async def ready(request):
        return web.Response(status=response_status)

    app = web.Application()
    app.router.add_get("/readyz", ready)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    health_file = tmp_path / "health.url"
    health_file.write_text(f"http://127.0.0.1:{port}/healthz?ignored=1")
    changed = asyncio.Event()
    manager = TunnelManager(lambda force: None, tmp_path, changed.set)
    task = asyncio.create_task(manager._watch_health_file(health_file))
    try:
        await asyncio.wait_for(changed.wait(), 2)
        assert manager.status.health_url is not None
        assert not manager.status.healthy
        assert manager.status.state == "running"
        changed.clear()
        response_status = 200
        await asyncio.wait_for(changed.wait(), 2)
        assert manager.status.healthy
        changed.clear()
        await runner.cleanup()
        await asyncio.wait_for(changed.wait(), 7)
        assert not manager.status.healthy
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await runner.cleanup()


async def _run_tunnel_manager_cleans_up_stale_client(tmp_path: Path) -> None:
    fake_client = tmp_path / "tunnel-client"
    fake_client.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib, sys, time\n"
        "health_file = pathlib.Path(sys.argv[sys.argv.index('--health.url-file') + 1])\n"
        "health_file.write_text('http://127.0.0.1:9/health')\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    fake_client.chmod(0o755)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    health_file = run_dir / "health.url"
    stale = await asyncio.create_subprocess_exec(
        str(fake_client),
        "run",
        "--health.url-file",
        str(health_file),
    )
    for _ in range(20):
        if health_file.exists():
            break
        await asyncio.sleep(0.1)

    manager = TunnelManager(lambda force: fake_client, run_dir)
    await manager.start(
        {
            CONF_TUNNEL_ID: "tunnel_0123456789abcdef0123456789abcdef",
            CONF_API_KEY: "runtime-key",
            CONF_HA_MCP_URL: "http://127.0.0.1:9584/private_secret",
            CONF_HA_MCP_BEARER_TOKEN: "",
            CONF_CONTROL_PLANE_BASE_URL: "",
            CONF_CONTROL_PLANE_PATH: "",
        }
    )

    await asyncio.wait_for(stale.wait(), timeout=5)
    assert stale.returncode is not None

    await manager.stop()
