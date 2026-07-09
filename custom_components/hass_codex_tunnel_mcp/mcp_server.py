"""Runtime HA-MCP server supervision."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import os
import secrets
import socket
import sys
from pathlib import Path

from .const import TUNNEL_CLIENT_VERSION

HA_MCP_PACKAGE = "ha-mcp"
HA_MCP_MODULE = "ha_mcp"


@dataclass
class MCPStatus:
    """Current HA-MCP state."""

    state: str = "stopped"
    port: int | None = None
    secret_path: str | None = None
    returncode: int | None = None
    last_error: str | None = None


def choose_loopback_port() -> int:
    """Reserve and return an available loopback TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def new_secret_path() -> str:
    """Create a non-guessable HTTP path for the local MCP server."""
    return f"/mcp/{secrets.token_urlsafe(32)}"


class HAMCPServerManager:
    """Install and run ha-mcp bound to loopback."""

    def __init__(self, hass, deps_dir: Path, notify) -> None:
        self._hass = hass
        self._deps_dir = deps_dir
        self._notify = notify
        self._process: asyncio.subprocess.Process | None = None
        self._watch_task: asyncio.Task[None] | None = None
        self.status = MCPStatus()
        self._access_token: str | None = None

    async def start(self) -> None:
        """Start the HA-MCP subprocess."""
        await self.stop()
        self._deps_dir.mkdir(parents=True, exist_ok=True)
        await self._ensure_package()
        self._access_token = await self._create_access_token()
        port = choose_loopback_port()
        secret_path = new_secret_path()
        command = [
            sys.executable,
            "-m",
            HA_MCP_MODULE,
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--path",
            secret_path,
        ]
        env = {
            **os.environ.copy(),
            "PYTHONPATH": str(self._deps_dir),
            "HOME_ASSISTANT_URL": self._internal_ha_url(),
            "HOME_ASSISTANT_TOKEN": self._access_token,
        }
        self.status = MCPStatus(state="starting", port=port, secret_path=secret_path)
        self._notify()
        try:
            self._process = await asyncio.create_subprocess_exec(
                *command,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except Exception as err:
            self.status = MCPStatus(
                state="error", port=port, secret_path=secret_path, last_error=str(err)
            )
            self._notify()
            raise
        self.status = MCPStatus(state="running", port=port, secret_path=secret_path)
        self._watch_task = asyncio.create_task(self._watch_process())
        self._notify()

    async def stop(self) -> None:
        """Stop the HA-MCP subprocess."""
        if self._watch_task is not None:
            self._watch_task.cancel()
            try:
                await self._watch_task
            except asyncio.CancelledError:
                pass
            self._watch_task = None
        process = self._process
        self._process = None
        if process is not None and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=10)
            except TimeoutError:
                process.kill()
                await process.wait()
        self.status.state = "stopped"
        self._notify()

    async def restart(self) -> None:
        """Restart HA-MCP."""
        await self.start()

    async def _watch_process(self) -> None:
        assert self._process is not None
        returncode = await self._process.wait()
        self.status.state = "exited"
        self.status.returncode = returncode
        if returncode:
            self.status.last_error = f"ha-mcp exited with status {returncode}"
        self._notify()

    async def _ensure_package(self) -> None:
        """Runtime-install ha-mcp into the integration storage area."""
        marker = self._deps_dir / f".{HA_MCP_PACKAGE}.installed"
        if marker.exists():
            return
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "pip",
            "install",
            "--target",
            str(self._deps_dir),
            HA_MCP_PACKAGE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode:
            detail = (stderr or stdout).decode(errors="replace")[-1000:]
            raise RuntimeError(f"failed to install {HA_MCP_PACKAGE}: {detail}")
        marker.write_text(TUNNEL_CLIENT_VERSION, encoding="utf-8")

    async def _create_access_token(self) -> str:
        """Create a local Home Assistant token for ha-mcp."""
        try:
            from homeassistant.auth.const import GROUP_ID_ADMIN
        except Exception as err:  # pragma: no cover - HA runtime path
            raise RuntimeError("Home Assistant auth APIs are unavailable") from err

        user = await self._hass.auth.async_create_system_user(
            "OpenAI Tunnel HA-MCP", group_ids=[GROUP_ID_ADMIN], local_only=True
        )
        refresh_token = await self._hass.auth.async_create_refresh_token(
            user, client_name="OpenAI Tunnel for HA-MCP"
        )
        return self._hass.auth.async_create_access_token(refresh_token)

    def _internal_ha_url(self) -> str:
        """Return the local HA API URL."""
        try:
            from homeassistant.helpers import network

            return network.get_url(
                self._hass,
                prefer_external=False,
                require_ssl=False,
                allow_internal=True,
            )
        except Exception:
            return "http://127.0.0.1:8123"
