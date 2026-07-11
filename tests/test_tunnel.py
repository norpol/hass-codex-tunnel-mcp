from __future__ import annotations

import asyncio
from pathlib import Path

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


async def _run_tunnel_manager_lifecycle_with_fake_client(tmp_path: Path) -> None:
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
    assert manager.status.health_url == "http://127.0.0.1:9/health"
    assert notifications > 0

    await manager.stop()
    assert manager.status.state == "stopped"


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
