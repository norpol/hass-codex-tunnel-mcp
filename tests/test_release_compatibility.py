"""Opt-in tests of verified release binaries against loopback HTTP fixtures.

Set TUNNEL_CLIENT_ARCHIVE_DIR to downloaded release ZIPs (see docs). No live
Home Assistant instance, OpenAI credentials, or external service is used.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, Mock
from urllib.parse import urlsplit

import pytest
import pytest_asyncio
from aiohttp import ClientSession, web

from custom_components.hass_codex_tunnel_mcp import binary as binary_module
from custom_components.hass_codex_tunnel_mcp import updater as updater_module
from custom_components.hass_codex_tunnel_mcp.binary import (
    TunnelClientAsset,
    TunnelClientError,
    install_from_zip_bytes,
    normalize_arch,
    parse_github_releases,
)
from custom_components.hass_codex_tunnel_mcp.const import (
    CONF_API_KEY,
    CONF_CONTROL_PLANE_BASE_URL,
    CONF_HA_MCP_BEARER_TOKEN,
    CONF_HA_MCP_URL,
    CONF_TUNNEL_ID,
)
from custom_components.hass_codex_tunnel_mcp.health import summarize_health_payload
from custom_components.hass_codex_tunnel_mcp.tunnel import TunnelManager
from custom_components.hass_codex_tunnel_mcp.updater import (
    TunnelClientUpdater,
    TunnelClientUpdaterState,
)

TUNNEL_ID = "tunnel_0123456789abcdef0123456789abcdef"
RELEASE_PUBLISHED_AT = {
    "v0.0.19": datetime(2026, 10, 30, 20, 26, 10, tzinfo=UTC),
    "v0.0.20": datetime(2026, 11, 6, 22, 5, 17, tzinfo=UTC),
}
RELEASE_DIGESTS = {
    "v0.0.18": {
        "amd64": "3c82307316acd93c88a384b62af1d81d3f4cb0efa975f9c0bfaef922866a04c4",
        "arm64": "a5f3d70efc75f5e66ab5c67c44d272aa80b2736f93f875f165140517750ec035",
    },
    "v0.0.19": {
        "amd64": "9ee9448592345a9dd1517fb79791ab54d24f051142eb92bcbe8cd355effc9142",
        "arm64": "5a19e556c4a8736ab7a6fde6f0568f6e7411fa2dff21186fc15bc30e9b98a603",
    },
    "v0.0.20": {
        "amd64": "2f06ce5a17425c74a827e7038692936e973ae388e7eec728c40efee1fb5f139d",
        "arm64": "2d89946c23111eb910f1bd4e7c5c4bfedfd73ac0dc9828aefc36294cfeae491f",
    },
}


@pytest.fixture
def release_binaries(tmp_path):
    directory = os.environ.get("TUNNEL_CLIENT_ARCHIVE_DIR")
    if not directory:
        pytest.skip("set TUNNEL_CLIENT_ARCHIVE_DIR to run release binary tests")
    arch = normalize_arch()
    if arch not in RELEASE_DIGESTS["v0.0.19"]:
        pytest.skip(f"unsupported test architecture: {arch}")
    result = {}
    for version, digests in RELEASE_DIGESTS.items():
        asset = TunnelClientAsset(
            "linux",
            arch,
            f"tunnel-client-{version}-linux-{arch}.zip",
            digests[arch],
            version=version,
        )
        archive = Path(directory) / asset.filename
        # Explicit opt-in with missing/mismatched archives is a failure.
        executable = install_from_zip_bytes(
            archive.read_bytes(), asset, tmp_path / "bin" / version
        )
        result[version] = (asset, executable)
    return result


class LocalTunnel:
    """Small control-plane and MCP fixtures, using the upstream wire contract."""

    def __init__(self):
        self.commands = asyncio.Queue()
        self.responses = {}
        self.mcp_requests = []
        self.control_requests = []
        self.control_status = 200
        self.mcp_status = 200
        self.base_url = ""
        self.challenge = "Bearer"
        self.metadata_handler = None

    def entry(self, static_token=True):
        return {
            CONF_TUNNEL_ID: TUNNEL_ID,
            CONF_API_KEY: "fixture-runtime-key",
            CONF_HA_MCP_URL: self.base_url + "/api/mcp",
            CONF_HA_MCP_BEARER_TOKEN: "fixture-ha-token" if static_token else "",
            CONF_CONTROL_PLANE_BASE_URL: self.base_url,
        }

    async def control(self, request):
        self.control_requests.append(request.headers.copy())
        if request.headers.get("Authorization") != "Bearer fixture-runtime-key":
            return web.Response(status=401)
        if self.control_status != 200:
            return web.Response(status=self.control_status)
        if request.path.endswith("/poll"):
            try:
                command = await asyncio.wait_for(self.commands.get(), 0.2)
            except TimeoutError:
                return web.Response(status=204)
            return web.json_response({"commands": [command]})
        if request.path.endswith("/response"):
            body = await request.json()
            future = self.responses.get(body["request_id"])
            if future is not None and not future.done():
                future.set_result(body)
            return web.json_response({"status": "ok"})
        if request.path == f"/v1/tunnels/{TUNNEL_ID}":
            return web.json_response({"id": TUNNEL_ID, "name": "local fixture"})
        return web.Response(status=404)

    async def mcp(self, request):
        headers = request.headers.copy()
        body = (
            await request.json()
            if request.method == "POST" and request.can_read_body
            else {}
        )
        self.mcp_requests.append((headers, body))
        if request.headers.get("Authorization") != "Bearer fixture-ha-token":
            return web.Response(
                status=401, headers={"WWW-Authenticate": self.challenge}
            )
        if self.mcp_status != 200:
            return web.Response(status=self.mcp_status)
        if request.method != "POST":
            return web.Response(status=405)
        method = body.get("method")
        if method == "notifications/initialized":
            return web.Response(status=202)
        response_headers = {}
        if method == "initialize":
            result = {
                "protocolVersion": body["params"]["protocolVersion"],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "loopback-mcp", "version": "1"},
            }
            response_headers["Mcp-Session-Id"] = "fixture-session"
        elif method == "tools/list":
            result = {"tools": [{"name": "echo", "inputSchema": {"type": "object"}}]}
        elif method == "tools/call":
            mode = body["params"].get("arguments", {}).get("request_id", "present")
            if mode != "missing":
                response_headers["X-Request-Id"] = "mcp-response-id"
            if mode == "hop-by-hop":
                response_headers["Connection"] = "X-Request-Id"
            result = {"content": [{"type": "text", "text": "fixture-ok"}]}
        else:
            result = {}
        return web.json_response(
            {"jsonrpc": "2.0", "id": body.get("id"), "result": result},
            headers=response_headers,
        )

    async def metadata(self, request):
        if self.metadata_handler is not None:
            return await self.metadata_handler(request)
        return web.Response(status=404)

    async def rpc(
        self,
        method,
        params=None,
        *,
        auth="fixture-ha-token",
        notification=False,
        channel="main",
    ):
        number = len(self.responses)
        queue_id, rpc_id = f"queue-{number}", f"rpc-{number}"
        body = {"jsonrpc": "2.0", "method": method}
        if not notification:
            body["id"] = rpc_id
        if params is not None:
            body["params"] = params
        headers = {
            "Content-Type": ["application/json"],
            "Accept": ["application/json, text/event-stream"],
            "Mcp-Protocol-Version": ["2025-11-25"],
            "X-Request-Id": ["connector-request-id"],
        }
        if channel == "harpoon":
            headers["Mcp-Protocol-Version"] = ["2026-07-28"]
            body.setdefault("params", {})["_meta"] = {
                "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                "io.modelcontextprotocol/clientInfo": {
                    "name": "oauth-fixture",
                    "version": "1",
                },
                "io.modelcontextprotocol/clientCapabilities": {},
            }
        if method != "initialize" and channel == "main":
            headers["Mcp-Session-Id"] = ["fixture-session"]
        if auth:
            headers["Authorization"] = [f"Bearer {auth}"]
        future = asyncio.get_running_loop().create_future()
        self.responses[queue_id] = future
        await self.commands.put(
            {
                "command_type": "jsonrpc",
                "request_id": queue_id,
                "channel": channel,
                "jsonrpc": body,
                "headers": headers,
                "shard_token": queue_id,
                "created_at": datetime.now(UTC).isoformat(),
            }
        )
        return await asyncio.wait_for(future, 15)


@pytest_asyncio.fixture
async def local_tunnel(release_binaries, monkeypatch, tmp_path):
    # Prevent inherited runtime settings/proxies affecting
    # the fixture. TunnelManager supplies the two fake secrets itself.
    for name in list(os.environ):
        if name == "TUNNEL_CLIENT_ARCHIVE_DIR":
            continue
        if name.startswith(
            ("MCP_", "CONTROL_PLANE_", "TUNNEL_", "HEALTH_", "HARPOON_")
        ) or name in {
            "OPENAI_API_KEY",
            "CA_BUNDLE",
            "HA_MCP_AUTH_HEADER",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "http_proxy",
            "https_proxy",
            "all_proxy",
        }:
            monkeypatch.delenv(name)
    monkeypatch.setenv("TUNNEL_CLIENT_PROFILE_DIR", str(tmp_path / "profiles"))
    monkeypatch.setenv("LOG_LEVEL", "error")
    monkeypatch.setenv("LOG_FORMAT", "struct-text")
    fixture = LocalTunnel()
    app = web.Application()
    app.router.add_route("*", "/api/mcp", fixture.mcp)
    app.router.add_route("*", "/v1/tunnels/{tail:.*}", fixture.control)
    app.router.add_route("GET", "/{tail:.*}", fixture.metadata)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    fixture.base_url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    try:
        yield fixture
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
@pytest.mark.parametrize("version", list(RELEASE_DIGESTS))
@pytest.mark.parametrize(
    "static_token", [True, False], ids=["static-auth", "forwarded-auth"]
)
async def test_release_http_auth_health_and_request_ids(
    release_binaries,
    local_tunnel,
    tmp_path,
    version,
    static_token,
):
    fixture = local_tunnel
    manager = TunnelManager(
        lambda force: release_binaries[version][1], tmp_path / "run"
    )
    entry = fixture.entry(static_token)
    try:
        await manager.start(entry)
        assert await manager.wait_until_healthy(15), manager.status
        assert manager.status.version == version
        initialized = await fixture.rpc(
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "release-test", "version": "1"},
            },
            auth=None if static_token else "fixture-ha-token",
        )
        assert initialized["resp_code"] == 200
        assert initialized["resp_headers"]["Mcp-Session-Id"] == ["fixture-session"]
        await fixture.rpc("notifications/initialized", notification=True)
        listed = await fixture.rpc("tools/list")
        assert listed["resp_json"]["result"]["tools"][0]["name"] == "echo"
        for mode in ("present", "missing", "hop-by-hop"):
            reply = await fixture.rpc(
                "tools/call",
                {
                    "name": "echo",
                    "arguments": {"request_id": mode},
                },
                auth=None if static_token else "fixture-ha-token",
            )
            assert reply["resp_code"] == 200
            assert reply["resp_json"]["result"]["content"][0]["text"] == "fixture-ok"
            response_headers = reply.get("resp_headers", {})
            expected = (
                ["mcp-response-id"]
                if version != "v0.0.18" and mode == "present"
                else None
            )
            assert response_headers.get("X-Tunnel-Mcp-Request-Id") == expected
            assert "X-Request-Id" not in response_headers
        calls = [
            (h, b) for h, b in fixture.mcp_requests if b.get("method") == "tools/call"
        ]
        assert len(calls) == 3
        assert all(h["Authorization"] == "Bearer fixture-ha-token" for h, _ in calls)
        assert all(h["X-Request-Id"] == "connector-request-id" for h, _ in calls)
        assert all(h["Mcp-Session-Id"] == "fixture-session" for h, _ in calls)
        assert all(
            h["Authorization"] == "Bearer fixture-runtime-key"
            for h in fixture.control_requests
        )
        assert "fixture-ha-token" not in str(fixture.control_requests)
        if static_token:
            # The startup probe must use the same environment-backed HA token.
            assert (
                fixture.mcp_requests[0][0]["Authorization"] == "Bearer fixture-ha-token"
            )

        if not static_token:
            for auth in (None, "wrong-token"):
                denied = await fixture.rpc("tools/list", auth=auth)
                assert denied["resp_code"] == 401
                assert denied["resp_headers"]["Www-Authenticate"] == ["Bearer"]

        base_url = (
            urlsplit(manager.status.health_url)
            ._replace(path="", query="", fragment="")
            .geturl()
        )
        before = len(fixture.mcp_requests)
        async with ClientSession() as session:
            async with session.get(base_url + "/health?details=true") as response:
                assert response.status == 200
                payload = await response.json()
            summary = summarize_health_payload(payload)
            assert summary.live and summary.ready
            assert (
                summary.components["mcp"]["details"]["transport"] == "http-streamable"
            )
            assert "fixture-ha-token" not in str(summary)
            for endpoint in ("/readyz", "/metrics", "/api/status"):
                async with session.get(base_url + endpoint) as response:
                    assert response.status == 200
        assert len(fixture.mcp_requests) == before, "health reads must remain passive"

        # An HTTP MCP failure does not involve stdio recovery; later requests
        # still work once the server recovers, without restarting the client.
        fixture.mcp_status = 503
        assert (await fixture.rpc("tools/list"))["resp_code"] == 503
        fixture.mcp_status = 200
        assert (await fixture.rpc("tools/list"))["resp_code"] == 200

        # Unexpected client exit is reported, then the existing restart path
        # recovers. This is whole-process supervision, not stdio child recovery.
        process = manager.process
        process.kill()
        await process.wait()
        async with asyncio.timeout(5):
            while manager.status.state != "exited":
                await asyncio.sleep(0.01)
        assert not manager.status.healthy
        assert manager.status.returncode != 0
        await manager.restart(entry)
        assert await manager.wait_until_healthy(15)
        assert manager.process.pid != process.pid
        assert (await fixture.rpc("tools/list"))["resp_code"] == 200
    finally:
        await manager.stop()
    assert manager.process is None
    assert manager.status.state == "stopped"


@pytest.mark.asyncio
@pytest.mark.parametrize("version", list(RELEASE_DIGESTS))
async def test_release_control_plane_auth_failure_is_diagnostic(
    release_binaries,
    local_tunnel,
    tmp_path,
    version,
):
    """Readiness alone is not evidence of successful control-plane auth."""
    entry = local_tunnel.entry()
    entry[CONF_API_KEY] = "wrong-runtime-key"
    manager = TunnelManager(
        lambda force: release_binaries[version][1], tmp_path / "run"
    )
    try:
        await manager.start(entry)
        # This is the existing upstream contract in all tested releases: /readyz
        # gates MCP/OAuth startup, while polling auth errors are component data.
        assert await manager.wait_until_healthy(15)
        base_url = (
            urlsplit(manager.status.health_url)
            ._replace(path="", query="", fragment="")
            .geturl()
        )
        async with ClientSession() as session:
            async with asyncio.timeout(10):
                while True:
                    async with session.get(
                        base_url + "/health?details=true"
                    ) as response:
                        summary = summarize_health_payload(await response.json())
                    control = summary.components["control-plane"]["details"]
                    if control.get("http_status") == 401:
                        break
                    await asyncio.sleep(0.1)
        assert control["failure_category"] == "http_error"
        assert control["consecutive_failures"] >= 1
        assert summary.ready is True
        assert "wrong-runtime-key" not in str(summary)
        assert "fixture-ha-token" not in str(local_tunnel.control_requests)
    finally:
        await manager.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("candidate_ready", [True, False])
@pytest.mark.parametrize(
    ("old_version", "new_version", "manual_early"),
    [
        ("v0.0.18", "v0.0.19", False),
        ("v0.0.18", "v0.0.20", False),
        ("v0.0.19", "v0.0.20", False),
        ("v0.0.19", "v0.0.20", True),
    ],
)
async def test_release_automatic_update_and_rollback(
    release_binaries,
    local_tunnel,
    tmp_path,
    monkeypatch,
    candidate_ready,
    old_version,
    new_version,
    manual_early,
):
    old_asset, _ = release_binaries[old_version]
    new_asset, _ = release_binaries[new_version]
    eligible_at = RELEASE_PUBLISHED_AT[new_version] + timedelta(days=8)
    install_at = (
        RELEASE_PUBLISHED_AT[new_version] + timedelta(days=1)
        if manual_early
        else eligible_at
    )
    install_options = (
        {"selected_version": new_version} if manual_early else {"automatic": True}
    )
    releases = parse_github_releases(
        [
            {
                "tag_name": new_version,
                "published_at": RELEASE_PUBLISHED_AT[new_version].isoformat(),
                "draft": False,
                "prerelease": False,
                "assets": [
                    {
                        "name": new_asset.filename,
                        "digest": "sha256:" + new_asset.sha256,
                        "browser_download_url": new_asset.url,
                    }
                ],
            }
        ]
    )
    monkeypatch.setattr(
        updater_module, "fetch_github_releases", AsyncMock(return_value=releases)
    )
    monkeypatch.setattr(updater_module, "create_issue", AsyncMock())
    monkeypatch.setattr(updater_module, "delete_issue", AsyncMock())
    # Use a separate installation root so the updater really installs the
    # candidate. Only the network download is replaced with the official ZIP.
    install_root = tmp_path / "update-bin"
    archive_dir = Path(os.environ["TUNNEL_CLIENT_ARCHIVE_DIR"])
    install_from_zip_bytes(
        (archive_dir / old_asset.filename).read_bytes(),
        old_asset,
        install_root / old_version,
    )
    download = Mock(return_value=(archive_dir / new_asset.filename).read_bytes())
    monkeypatch.setattr(binary_module, "_download_bytes", download)
    updater = TunnelClientUpdater(Mock(), Mock(), install_root, Mock())
    updater._lock = asyncio.Lock()
    # Existing pinned installs may have no saved asset metadata.
    updater.state = TunnelClientUpdaterState(active_version=old_asset.version)
    manager = TunnelManager(
        lambda force: updater.ensure_active_executable(force=force), tmp_path / "run"
    )
    entry = local_tunnel.entry()
    try:
        await manager.start(entry)
        assert await manager.wait_until_healthy(15)
        previous_process = manager.process
        deferred = await updater.async_update_tunnel_client(
            manager,
            entry,
            now=install_at - timedelta(microseconds=1),
            automatic=True,
        )
        assert deferred.candidate_asset is None
        assert deferred.deferred_until == eligible_at.isoformat()
        assert manager.process is previous_process
        download.assert_not_called()
        if not candidate_ready:
            # Reject only the candidate's MCP startup probe. The actual
            # readiness watcher and real old binary perform failure/rollback.
            original_start = manager.start

            async def start_with_unready_candidate(data, **kwargs):
                executable = kwargs.get("executable_override")
                local_tunnel.mcp_status = 503 if executable is not None else 200
                await original_start(data, **kwargs)

            monkeypatch.setattr(manager, "start", start_with_unready_candidate)
            monkeypatch.setattr(updater_module, "READINESS_TIMEOUT", 3)
            with pytest.raises(TunnelClientError, match="did not become ready"):
                await updater.async_update_tunnel_client(
                    manager, entry, now=install_at, **install_options
                )
            assert manager.status.version == updater.state.active_version == old_version
            assert manager.status.healthy
            download.assert_called_once_with(new_asset.url)
            assert new_version in updater.state.failed_versions
            assert updater.state.last_update is None
            assert (await local_tunnel.rpc("tools/list"))["resp_code"] == 200
            rolled_back_process = manager.process
            skipped = await updater.async_update_tunnel_client(
                manager, entry, now=eligible_at + timedelta(days=1), automatic=True
            )
            assert skipped.candidate_asset is None
            assert manager.process is rolled_back_process
            return
        await updater.async_update_tunnel_client(
            manager, entry, now=install_at, **install_options
        )
        download.assert_called_once_with(new_asset.url)
        assert previous_process.returncode == 0
        assert updater.state.active_version == new_version
        assert manager.status.version == new_version
        assert (await local_tunnel.rpc("tools/list"))["resp_code"] == 200
        assert updater.state.previous_version == old_version
        assert updater.state.previous_asset is None
        assert await updater.async_rollback(manager, entry)
        assert manager.status.version == updater.state.active_version == old_version
        assert (await local_tunnel.rpc("tools/list"))["resp_code"] == 200
    finally:
        await manager.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("version", ["v0.0.19", "v0.0.20"])
@pytest.mark.parametrize("body_timeout", [False, True], ids=["headers", "body"])
async def test_release_oauth_timeout_with_404_recovers(
    release_binaries, local_tunnel, tmp_path, monkeypatch, caplog, version, body_timeout
):
    """An actual five-second discovery deadline, with a missing path fallback."""
    import logging

    caplog.set_level(logging.INFO)
    monkeypatch.setenv("LOG_LEVEL", "info")
    root_hits = 0
    paths = []
    first_request = asyncio.Event()
    release_first_request = asyncio.Event()
    allow_recovered_response = asyncio.Event()

    async def metadata(request):
        nonlocal root_hits
        paths.append(request.path)
        if request.path != "/.well-known/oauth-protected-resource":
            return web.Response(status=404, text="not found")
        root_hits += 1
        if root_hits == 1:
            first_request.set()
            if body_timeout:
                response = web.StreamResponse(
                    headers={"Content-Type": "application/json"}
                )
                await response.prepare(request)
                await response.write(b'{"resource":')
                await release_first_request.wait()
                return response
            await release_first_request.wait()
        await allow_recovered_response.wait()
        return web.json_response({"resource": local_tunnel.base_url + "/api/mcp"})

    local_tunnel.metadata_handler = metadata
    manager = TunnelManager(
        lambda force: release_binaries[version][1], tmp_path / "run"
    )
    try:
        await manager.start(local_tunnel.entry())
        await asyncio.wait_for(first_request.wait(), 5)
        assert not manager.status.healthy
        # A second root request proves a retry after the real deadline. The
        # v0.0.19 control permanently settles discovery after the mixed failure.
        async with asyncio.timeout(15):
            while root_hits < 2:
                if version == "v0.0.19" and "OAuth discovery failed" in caplog.text:
                    break
                await asyncio.sleep(0.05)
        assert "/.well-known/oauth-protected-resource/api/mcp" in paths
        assert not manager.status.healthy
        if version == "v0.0.20":
            assert root_hits >= 2
            allow_recovered_response.set()
            assert await manager.wait_until_healthy(10)
            assert (await local_tunnel.rpc("tools/list"))["resp_code"] == 200
        else:
            assert root_hits == 1
            assert "OAuth discovery failed; retrying" not in caplog.text
    finally:
        release_first_request.set()
        allow_recovered_response.set()
        await manager.stop()


@pytest.mark.asyncio
async def test_release_expected_auth_probe_is_info(
    release_binaries, local_tunnel, tmp_path, monkeypatch, caplog
):
    import logging

    monkeypatch.setenv("LOG_LEVEL", "info")
    caplog.set_level(logging.INFO)
    manager = TunnelManager(
        lambda force: release_binaries["v0.0.20"][1], tmp_path / "run"
    )
    try:
        await manager.start(local_tunnel.entry(static_token=False))
        assert await manager.wait_until_healthy(15)
        assert (await local_tunnel.rpc("tools/list"))["resp_code"] == 200
        assert (
            "MCP server requires authentication; startup probe reached server"
            in caplog.text
        )
        assert "failed to connect to mcp" not in caplog.text
        assert (
            "MCP startup probe completed with failure; starting control-plane poller for compatibility"
            not in caplog.text
        )
        # The HA log reader maps stderr to WARNING; the embedded upstream
        # record is INFO. Do not mistake HA's wrapper severity for a new error.
        lines = [
            r.message
            for r in caplog.records
            if "startup probe reached server" in r.message
        ]
        assert any("level=INFO" in line for line in lines)
    finally:
        await manager.stop()


@pytest_asyncio.fixture
async def oauth_origin(local_tunnel, tmp_path, monkeypatch):
    """TLS metadata and token endpoint on a different origin, with a real CA."""
    import ipaddress
    import ssl

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "loopback OAuth fixture")]
    )
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(days=1))
        .not_valid_after(datetime.now(UTC) + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_file, key_file = tmp_path / "ca.pem", tmp_path / "key.pem"
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_file, key_file)
    state = {"url": "", "requests": [], "third_hits": 0}

    async def third(request):
        state["third_hits"] += 1
        return web.Response(text="must not be reached")

    third_app = web.Application()
    third_app.router.add_route("*", "/{tail:.*}", third)
    third_runner = web.AppRunner(third_app)
    await third_runner.setup()
    third_site = web.TCPSite(third_runner, "127.0.0.1", 0, ssl_context=context)
    await third_site.start()
    third_url = f"https://127.0.0.1:{third_site._server.sockets[0].getsockname()[1]}"

    async def handler(request):
        state["requests"].append((request.path, dict(request.headers)))
        origin = state["url"]
        if request.path == "/metadata":
            return web.json_response(
                {
                    "resource": origin + "/resource",
                    "authorization_servers": [origin + "/issuer"],
                }
            )
        if request.path in (
            "/.well-known/oauth-authorization-server/issuer",
            "/issuer/.well-known/oauth-authorization-server",
        ):
            return web.json_response(
                {
                    "issuer": origin + "/issuer",
                    "authorization_endpoint": origin + "/authorize",
                    "token_endpoint": origin + "/token",
                    "registration_endpoint": origin + "/register",
                    "revocation_endpoint": origin + "/revoke",
                    "introspection_endpoint": third_url + "/introspect",
                    "response_types_supported": ["code"],
                    "code_challenge_methods_supported": ["S256"],
                }
            )
        if request.path == "/token":
            form = await request.post()
            if (
                form.get("code") != "fixture-code"
                or form.get("resource") != origin + "/resource"
            ):
                return web.Response(status=400)
            return web.json_response(
                {"access_token": "fixture-ha-token", "token_type": "Bearer"}
            )
        if request.path == "/revoke":
            raise web.HTTPTemporaryRedirect(third_url + "/redirect")
        return web.Response(status=404)

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=context)
    await site.start()
    state["url"] = f"https://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    local_tunnel.challenge = f'Bearer resource_metadata="{state["url"]}/metadata"'
    monkeypatch.setenv("CA_BUNDLE", str(cert_file))
    try:
        yield state
    finally:
        await runner.cleanup()
        await third_runner.cleanup()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("version", "forward"), [("v0.0.19", False), ("v0.0.20", False), ("v0.0.20", True)]
)
async def test_release_separate_oauth_origin(
    release_binaries,
    local_tunnel,
    oauth_origin,
    tmp_path,
    monkeypatch,
    version,
    forward,
):
    import base64
    import json
    from urllib.parse import urlencode

    origin = oauth_origin["url"]
    monkeypatch.setenv("MCP_OAUTH_TRUSTED_ORIGINS", origin)
    if forward:
        monkeypatch.setenv("HARPOON_OAUTH_FORWARD_ORIGINS", origin)
    # Retain an MCP-only discovery secret to detect cross-origin leakage.
    monkeypatch.setenv(
        "MCP_DISCOVERY_EXTRA_HEADERS", "X-MCP-Secret: fixture-mcp-secret"
    )
    manager = TunnelManager(
        lambda force: release_binaries[version][1], tmp_path / "run"
    )

    async def call(name, arguments):
        return await local_tunnel.rpc(
            "tools/call",
            {"name": name, "arguments": arguments},
            channel="harpoon",
            auth=None,
        )

    def structured(reply):
        assert reply["resp_code"] == 200, reply
        result = reply["resp_json"]["result"]
        assert not result.get("isError"), result
        return result["structuredContent"]

    def http_json(reply):
        result = structured(reply)
        assert result["status_code"] == 200
        return json.loads(base64.b64decode(result["body_base64"]))

    try:
        await manager.start(local_tunnel.entry(static_token=False))
        assert await manager.wait_until_healthy(15), manager.status
        listed = await call("list_targets", {"categories": ["oauth"]})
        assert any(path == "/metadata" for path, _ in oauth_origin["requests"])
        assert any(
            headers.get("X-MCP-Secret") == "fixture-mcp-secret"
            for headers, _ in local_tunnel.mcp_requests
        ), "the scoped discovery credential must actually reach its MCP origin"
        if not forward:
            assert listed["resp_code"] == 400
            assert "unsupported channel" in str(listed)
            assert (await local_tunnel.rpc("tools/list"))["resp_code"] == 200
            return
        targets = structured(listed)["targets"]
        labels = {}
        for target in targets:
            assert target["source"] == target["category"] == "oauth"
            for role in (
                "source-url",
                "auth-server-metadata",
                "token-endpoint",
                "revocation-endpoint",
            ):
                if role in target["tags"]:
                    labels[role] = target["label"]
            assert "introspection-endpoint" not in target["tags"]
        assert {"source-url", "token-endpoint", "revocation-endpoint"} <= labels.keys()
        prmd = http_json(
            await call("call_target", {"label": labels["source-url"], "method": "GET"})
        )
        assert prmd["resource"] == origin + "/resource"
        token = http_json(
            await call(
                "call_target",
                {
                    "label": labels["token-endpoint"],
                    "method": "POST",
                    "headers": {"Content-Type": "application/x-www-form-urlencoded"},
                    "body": urlencode(
                        {
                            "grant_type": "authorization_code",
                            "code": "fixture-code",
                            "resource": prmd["resource"],
                        }
                    ),
                },
            )
        )
        response = await local_tunnel.rpc(
            "tools/call", {"name": "echo", "arguments": {}}, auth=token["access_token"]
        )
        assert response["resp_code"] == 200
        for label in ("oauth-introspection-endpoint-0", labels["revocation-endpoint"]):
            denied = await call("call_target", {"label": label, "method": "POST"})
            envelope = denied["resp_json"]
            assert envelope.get("error") or envelope.get("result", {}).get("isError"), (
                denied
            )
    finally:
        await manager.stop()
        assert oauth_origin["third_hits"] == 0
        for _, headers in oauth_origin["requests"]:
            assert "Authorization" not in headers
            assert "X-MCP-Secret" not in headers


@pytest.mark.asyncio
async def test_release_forward_approval_does_not_grant_discovery_trust(
    release_binaries, local_tunnel, oauth_origin, tmp_path, monkeypatch, caplog
):
    import logging

    caplog.set_level(logging.INFO)
    monkeypatch.setenv("LOG_LEVEL", "info")
    monkeypatch.setenv("HARPOON_OAUTH_FORWARD_ORIGINS", oauth_origin["url"])
    manager = TunnelManager(
        lambda force: release_binaries["v0.0.20"][1], tmp_path / "run"
    )
    try:
        await manager.start(local_tunnel.entry(static_token=False))
        async with asyncio.timeout(10):
            while "OAuth discovery failed" not in caplog.text:
                await asyncio.sleep(0.05)
        assert not manager.status.healthy
        assert "destination origin is not trusted" in caplog.text
        assert oauth_origin["requests"] == []
    finally:
        await manager.stop()
