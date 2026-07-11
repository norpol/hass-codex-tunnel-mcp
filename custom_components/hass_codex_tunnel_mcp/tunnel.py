"""Supervise tunnel-client for OpenAI Tunnel for HA-MCP."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass
import logging
import os
from pathlib import Path

from .const import (
    CONF_API_KEY,
    CONF_CONTROL_PLANE_BASE_URL,
    CONF_CONTROL_PLANE_PATH,
    CONF_HA_MCP_BEARER_TOKEN,
    CONF_HA_MCP_URL,
    CONF_TUNNEL_ID,
    TUNNEL_CLIENT_VERSION,
)

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class TunnelCommandConfig:
    """Inputs required to build a tunnel-client command."""

    tunnel_id: str
    mcp_server_url: str
    run_dir: Path
    control_plane_base_url: str = ""
    control_plane_url_path: str = ""
    use_ha_mcp_bearer_token: bool = False


def build_mcp_server_url(url: str) -> str:
    """Build the channel-mapped local MCP server URL."""
    return f"channel=main,url={url}"


def build_tunnel_command(executable: Path, config: TunnelCommandConfig) -> list[str]:
    """Build the tunnel-client command line."""
    health_file = config.run_dir / "health.url"
    command = [
        str(executable),
        "run",
        "--control-plane.tunnel-id",
        config.tunnel_id,
        "--control-plane.api-key",
        "env:CONTROL_PLANE_API_KEY",
        "--mcp.server-url",
        build_mcp_server_url(config.mcp_server_url),
        "--health.listen-addr",
        "127.0.0.1:0",
        "--health.url-file",
        str(health_file),
    ]
    if config.control_plane_base_url:
        command.extend(["--control-plane.base-url", config.control_plane_base_url])
    if config.control_plane_url_path:
        command.extend(["--control-plane.url-path", config.control_plane_url_path])
    if config.use_ha_mcp_bearer_token:
        command.extend(
            [
                "--mcp.extra-headers",
                "Authorization: env:HA_MCP_AUTH_HEADER",
                "--mcp.discovery-extra-headers",
                "Authorization: env:HA_MCP_AUTH_HEADER",
            ]
        )
    return command


@dataclass
class TunnelStatus:
    """Current tunnel process state."""

    state: str = "stopped"
    healthy: bool = False
    version: str = TUNNEL_CLIENT_VERSION
    health_url: str | None = None
    returncode: int | None = None
    last_error: str | None = None


class TunnelManager:
    """Small asyncio subprocess supervisor for tunnel-client."""

    def __init__(
        self,
        executable_provider: Callable[[bool], object],
        run_dir: Path,
        notify: Callable[[], None] | None = None,
    ) -> None:
        self._executable_provider = executable_provider
        self._run_dir = run_dir
        self._notify = notify or (lambda: None)
        self._process: asyncio.subprocess.Process | None = None
        self._watch_task: asyncio.Task[None] | None = None
        self._log_tasks: list[asyncio.Task[None]] = []
        self.status = TunnelStatus()

    @property
    def process(self) -> asyncio.subprocess.Process | None:
        """Return the current subprocess."""
        return self._process

    async def start(
        self,
        entry_data: Mapping[str, object],
        *,
        force_download: bool = False,
    ) -> None:
        """Start tunnel-client."""
        await self.stop()
        self._run_dir.mkdir(parents=True, exist_ok=True)
        health_file = self._run_dir / "health.url"
        if health_file.exists():
            health_file.unlink()

        executable = await self._maybe_await(self._executable_provider(force_download))
        tunnel_id = str(entry_data[CONF_TUNNEL_ID])
        command = build_tunnel_command(
            Path(executable),
            TunnelCommandConfig(
                tunnel_id=tunnel_id,
                mcp_server_url=str(entry_data[CONF_HA_MCP_URL]),
                run_dir=self._run_dir,
                control_plane_base_url=str(
                    entry_data.get(CONF_CONTROL_PLANE_BASE_URL) or ""
                ),
                control_plane_url_path=str(
                    entry_data.get(CONF_CONTROL_PLANE_PATH) or ""
                ),
                use_ha_mcp_bearer_token=bool(
                    str(entry_data.get(CONF_HA_MCP_BEARER_TOKEN) or "").strip()
                ),
            ),
        )
        env = os.environ.copy()
        env["CONTROL_PLANE_API_KEY"] = str(entry_data[CONF_API_KEY])
        ha_mcp_token = str(entry_data.get(CONF_HA_MCP_BEARER_TOKEN) or "").strip()
        if ha_mcp_token:
            env["HA_MCP_AUTH_HEADER"] = f"Bearer {ha_mcp_token}"
        self.status = TunnelStatus(state="starting")
        self._notify()
        try:
            self._process = await asyncio.create_subprocess_exec(
                *command,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except Exception as err:
            self.status = TunnelStatus(state="error", last_error=str(err))
            self._notify()
            raise

        self.status = TunnelStatus(state="running")
        self._start_log_tasks()
        self._watch_task = asyncio.create_task(self._watch_process(health_file))
        self._notify()

    async def stop(self) -> None:
        """Stop tunnel-client if it is running."""
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
        await self._stop_log_tasks()
        self.status = TunnelStatus(state="stopped")
        self._notify()

    async def restart(
        self, entry_data: Mapping[str, object]
    ) -> None:
        """Restart tunnel-client with existing binary state."""
        await self.start(entry_data)

    async def redownload(self, entry_data: Mapping[str, object]) -> None:
        """Force a fresh binary download and restart."""
        await self.start(
            entry_data,
            force_download=True,
        )

    async def _watch_process(self, health_file: Path) -> None:
        assert self._process is not None
        process = self._process
        health_task = asyncio.create_task(self._watch_health_file(health_file))
        try:
            returncode = await process.wait()
        finally:
            health_task.cancel()
            try:
                await health_task
            except asyncio.CancelledError:
                pass

        self.status.state = "exited"
        self.status.healthy = False
        self.status.returncode = returncode
        if returncode:
            self.status.last_error = f"tunnel-client exited with status {returncode}"
        await self._stop_log_tasks()
        self._notify()

    def _start_log_tasks(self) -> None:
        if self._process is None:
            return
        if self._process.stdout is not None:
            self._log_tasks.append(
                asyncio.create_task(self._log_stream(self._process.stdout, logging.INFO))
            )
        if self._process.stderr is not None:
            self._log_tasks.append(
                asyncio.create_task(
                    self._log_stream(self._process.stderr, logging.WARNING)
                )
            )

    async def _stop_log_tasks(self) -> None:
        for task in self._log_tasks:
            task.cancel()
        for task in self._log_tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._log_tasks.clear()

    async def _log_stream(
        self, stream: asyncio.StreamReader, level: int
    ) -> None:
        while line := await stream.readline():
            _LOGGER.log(level, "tunnel-client: %s", line.decode(errors="replace").rstrip())

    async def _watch_health_file(self, health_file: Path) -> None:
        while True:
            if health_file.exists():
                text = health_file.read_text(encoding="utf-8").strip()
                if text:
                    self.status.health_url = text
                    self.status.healthy = True
                    self.status.state = "healthy"
                    self._notify()
                    return
            await asyncio.sleep(0.5)

    @staticmethod
    async def _maybe_await(value: object) -> object:
        if hasattr(value, "__await__"):
            return await value  # type: ignore[misc]
        return value
