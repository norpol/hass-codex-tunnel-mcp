from __future__ import annotations

import asyncio
from pathlib import Path

from custom_components.hass_codex_tunnel_mcp.const import (
    CONF_API_KEY,
    CONF_CONTROL_PLANE_BASE_URL,
    CONF_CONTROL_PLANE_PATH,
    CONF_TUNNEL_ID,
)
from custom_components.hass_codex_tunnel_mcp.tunnel import (
    TunnelCommandConfig,
    TunnelManager,
    build_mcp_server_url,
    build_tunnel_command,
)


def test_build_mcp_server_url() -> None:
    assert (
        build_mcp_server_url(1234, "/secret")
        == "channel=main,url=http://127.0.0.1:1234/secret"
    )


def test_build_tunnel_command_includes_required_flags(tmp_path: Path) -> None:
    command = build_tunnel_command(
        tmp_path / "tunnel-client",
        TunnelCommandConfig(
            tunnel_id="tunnel_0123456789abcdef0123456789abcdef",
            mcp_port=8124,
            secret_path="/mcp/secret",
            run_dir=tmp_path,
            control_plane_base_url="https://control.example",
            control_plane_path="/v1",
        ),
    )

    assert command[:2] == [str(tmp_path / "tunnel-client"), "run"]
    assert "--control-plane.tunnel-id" in command
    assert "tunnel_0123456789abcdef0123456789abcdef" in command
    assert "--control-plane.api-key" in command
    assert "env:CONTROL_PLANE_API_KEY" in command
    assert "--mcp-server-url" in command
    assert "channel=main,url=http://127.0.0.1:8124/mcp/secret" in command
    assert "--health.listen-addr" in command
    assert "127.0.0.1:0" in command
    assert "--control-plane.base-url" in command
    assert "https://control.example" in command


def test_tunnel_manager_lifecycle_with_fake_client(tmp_path: Path) -> None:
    asyncio.run(_run_tunnel_manager_lifecycle_with_fake_client(tmp_path))


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
            CONF_CONTROL_PLANE_BASE_URL: "",
            CONF_CONTROL_PLANE_PATH: "",
        },
        mcp_port=1234,
        secret_path="/mcp/secret",
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
