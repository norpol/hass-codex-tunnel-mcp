from __future__ import annotations

import asyncio
import json
import logging
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


@pytest.mark.asyncio
@pytest.mark.parametrize("fallback", [logging.INFO, logging.WARNING])
@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("TRACE", logging.DEBUG),
        ("DEBUG", logging.DEBUG),
        ("INFO", logging.INFO),
        ("WARN", logging.WARNING),
        ("WARNING", logging.WARNING),
        ("ERROR", logging.ERROR),
        ("FATAL", logging.CRITICAL),
        ("CRITICAL", logging.CRITICAL),
    ],
)
@pytest.mark.parametrize("style", ["plain", "default", "struct-text", "json"])
async def test_log_stream_uses_message_severity(
    tmp_path, caplog, fallback, name, expected, style
):
    """Both pipes must honor the header, even when the body names another level."""
    message = "forwarded command; INFO WARN ERROR level=DEBUG"
    lines = {
        "plain": f"{name} {message}",
        # Go default logger and slog formats observed in v0.0.18 through v0.0.20.
        "default": f"2026/11/15 08:30:00 {name} {message}",
        "struct-text": f'time=2026-11-15T08:30:00.123Z level={name} msg="{message}"',
        "json": json.dumps(
            {"time": "2026-11-15T08:30:00Z", "level": name, "msg": message}
        ),
    }
    stream = asyncio.StreamReader()
    stream.feed_data((lines[style] + "\n").encode())
    stream.feed_eof()
    manager = TunnelManager(lambda force: None, tmp_path)
    caplog.set_level(logging.DEBUG)

    await manager._log_stream(stream, fallback)

    assert [(r.levelno, r.getMessage()) for r in caplog.records] == [
        (expected, f"tunnel-client: {lines[style]}")
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("fallback", [logging.INFO, logging.WARNING])
@pytest.mark.parametrize(
    "line",
    [
        b"unstructured diagnostic mentioning INFO and level=DEBUG",
        b"INFORMATION is not a severity",
        b"INFO_suffix is not a severity",
        b"level=INFO_suffix msg=unknown",
        b"level=NOTICE msg=unknown",
        b'time=2026-11-15T08:30:00Z msg="level=INFO"',
        b'msg="level=INFO" level=ERROR',
        b"2026/11/15 broken-time INFO malformed timestamp",
        b'{"level":"INFO",',
        b'{"level":"INFO"} trailing junk',
        b'{"level":"unknown","msg":"INFO"}',
        b'{"level":null}',
        b'{"level":30}',
        b'{"level":true}',
        b'{"level":[]}',
        b'{"level":{}}',
        b'{"msg":"INFO","context":{"level":"DEBUG"}}',
        b'[{"level":"INFO"}]',
        b'"INFO"',
        b"null",
        b"\xff malformed UTF-8 level=INFO",
        pytest.param(
            b'{"level":"INFO","context":' + b"[" * 1100,
            id="truncated-nested-json",
        ),
        pytest.param(
            b'{"level":"INFO","value":' + b"9" * 5000 + b"}",
            id="oversized-json-integer",
        ),
    ],
)
async def test_log_stream_falls_back_without_parsing_message_body(
    tmp_path, caplog, fallback, line
):
    """Malformed output cannot crash the reader or change the next line's level."""
    stream = asyncio.StreamReader()
    stream.feed_data(line + b"\nERROR next message\n")
    stream.feed_eof()
    manager = TunnelManager(lambda force: None, tmp_path)
    caplog.set_level(logging.DEBUG)

    await manager._log_stream(stream, fallback)

    assert [r.levelno for r in caplog.records] == [fallback, logging.ERROR]
    assert caplog.records[0].getMessage() == (
        "tunnel-client: " + line.decode(errors="replace")
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("fallback", [logging.INFO, logging.WARNING])
async def test_log_stream_ignores_duplicate_json_level(tmp_path, caplog, fallback):
    stream = asyncio.StreamReader()
    stream.feed_data(b'{"level":"ERROR","msg":"failure","level":"DEBUG"}\n')
    stream.feed_eof()
    manager = TunnelManager(lambda force: None, tmp_path)
    caplog.set_level(logging.DEBUG)

    await manager._log_stream(stream, fallback)

    assert [record.levelno for record in caplog.records] == [logging.ERROR]


@pytest.mark.asyncio
async def test_log_tasks_handle_both_pipes_and_eof(tmp_path, caplog):
    stdout, stderr = asyncio.StreamReader(), asyncio.StreamReader()
    stdout.feed_data(b"\n \t\n\x1b[31mERROR\x1b[0m stdout error\r\nstdout fallback")
    stderr.feed_data(b" \tinfo: stderr info\nlevel=debug msg=details\nstderr fallback")
    stdout.feed_eof()
    stderr.feed_eof()
    manager = TunnelManager(lambda force: None, tmp_path)
    manager._process = Mock(stdout=stdout, stderr=stderr)
    caplog.set_level(logging.DEBUG)

    manager._start_log_tasks()
    await asyncio.gather(*manager._log_tasks)
    await manager._stop_log_tasks()

    assert sorted((r.levelno, r.getMessage()) for r in caplog.records) == sorted(
        [
            (logging.ERROR, "tunnel-client: ERROR stdout error"),
            (logging.INFO, "tunnel-client: stdout fallback"),
            (logging.INFO, "tunnel-client:  \tinfo: stderr info"),
            (logging.DEBUG, "tunnel-client: level=debug msg=details"),
            (logging.WARNING, "tunnel-client: stderr fallback"),
        ]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("style", ["plain", "json", "json-unicode"])
async def test_log_stream_masks_configured_secrets(
    tmp_path, monkeypatch, caplog, style
):
    entry = {
        CONF_TUNNEL_ID: "tunnel_0123456789abcdef0123456789abcdef",
        CONF_API_KEY: 'fixture-key-with-"quote-é',
        CONF_HA_MCP_BEARER_TOKEN: "fixture-token",
        CONF_HA_MCP_URL: "http://127.0.0.1:8123/private-fixture-path",
    }
    secrets = [
        entry[key] for key in (CONF_API_KEY, CONF_HA_MCP_BEARER_TOKEN, CONF_HA_MCP_URL)
    ]
    payload = "Authorization: Bearer " + " ".join(secrets)
    line = (
        "ERROR " + payload
        if style == "plain"
        else json.dumps(
            {"level": "ERROR", "msg": payload}, ensure_ascii=style == "json"
        )
    )
    stdout, stderr = asyncio.StreamReader(), asyncio.StreamReader()
    stdout.feed_eof()
    # Include malformed/unrecognized output: fallback must redact as well.
    stderr.feed_data((line + "\nunknown " + payload + "\n").encode())
    stderr.feed_eof()
    process = Mock(stdout=stdout, stderr=stderr, returncode=0)
    monkeypatch.setattr(
        asyncio, "create_subprocess_exec", AsyncMock(return_value=process)
    )
    manager = TunnelManager(lambda force: tmp_path / "tunnel-client", tmp_path / "run")
    monkeypatch.setattr(manager, "_watch_process", AsyncMock())
    caplog.set_level(logging.DEBUG)
    try:
        await manager.start(entry)
        await asyncio.gather(*manager._log_tasks)
        assert [r.levelno for r in caplog.records] == [logging.ERROR, logging.WARNING]
        for record in caplog.records:
            assert record.getMessage().count("**REDACTED**") == 3
            for secret in secrets:
                assert secret not in record.getMessage()
                assert json.dumps(secret)[1:-1] not in record.args[0]
    finally:
        await manager.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("inherited_mode", ["auto", "disabled"])
async def test_restart_recomputes_oauth_mode(tmp_path, monkeypatch, inherited_mode):
    """Changing credentials must not retain an old or inherited discovery mode."""
    monkeypatch.setenv("MCP_OAUTH_DISCOVERY", inherited_mode)
    process = Mock(stdout=None, stderr=None, returncode=0)
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    manager = TunnelManager(lambda force: tmp_path / "tunnel-client", tmp_path / "run")
    monkeypatch.setattr(manager, "_watch_process", AsyncMock())
    entry = {
        CONF_TUNNEL_ID: "tunnel_0123456789abcdef0123456789abcdef",
        CONF_API_KEY: "fixture-key",
        CONF_HA_MCP_URL: "http://127.0.0.1:8123/api/mcp",
    }
    try:
        for token, mode in [
            (" token ", "disabled"),
            (" \t", "auto"),
            (None, "auto"),
            ("other-token", "disabled"),
        ]:
            entry[CONF_HA_MCP_BEARER_TOKEN] = token
            await manager.restart(entry)
            args, kwargs = spawn.call_args
            assert kwargs["env"]["MCP_OAUTH_DISCOVERY"] == mode
            assert not any(arg.startswith("--mcp.oauth-discovery") for arg in args)
            if mode == "disabled":
                assert kwargs["env"]["HA_MCP_AUTH_HEADER"] == f"Bearer {token.strip()}"
                assert "Authorization: env:HA_MCP_AUTH_HEADER" in args
            else:
                assert "Authorization: env:HA_MCP_AUTH_HEADER" not in args
    finally:
        await manager.stop()


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
