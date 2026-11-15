from __future__ import annotations

from io import BytesIO
import traceback
from unittest.mock import Mock
from urllib.error import HTTPError, URLError

import pytest

from custom_components.hass_codex_tunnel_mcp import mcp_url
from custom_components.hass_codex_tunnel_mcp.mcp_url import (
    MCPProbeError,
    async_probe_mcp_url,
    assess_mcp_url,
    normalize_mcp_url,
    redact_mcp_url,
)


def test_redact_mcp_url_removes_secret_path() -> None:
    assert (
        redact_mcp_url("http://127.0.0.1:9584/private_secret?x=1")
        == "http://127.0.0.1:9584/**REDACTED**"
    )


def test_assess_mcp_url_allows_loopback() -> None:
    assessment = assess_mcp_url("http://127.0.0.1:9584/private_secret")

    assert assessment.is_local_or_private is True
    assert assessment.warning is None


def test_normalize_mcp_url_allows_home_assistant_api_mcp_path() -> None:
    assert (
        normalize_mcp_url("http://127.0.0.1:8123/api/mcp/")
        == "http://127.0.0.1:8123/api/mcp"
    )


def test_assess_mcp_url_warns_on_public_url() -> None:
    assessment = assess_mcp_url("https://example.com/private_secret")

    assert assessment.is_local_or_private is False
    assert assessment.warning == "public_mcp_url"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [200, 401, 403, 405])
@pytest.mark.parametrize("token", ["", "test-ha-token"])
async def test_probe_accepts_reachable_endpoint(monkeypatch, status, token) -> None:
    """Reachability does not require successful auth or GET support."""
    url = "http://127.0.0.1:8123/api/mcp"

    def respond(request, *, timeout):
        assert request.full_url == url
        assert request.get_method() == "GET"
        assert request.get_header("Authorization") == (
            f"Bearer {token}" if token else None
        )
        assert timeout == 5.0
        if status >= 400:
            raise HTTPError(url, status, "test response", {}, BytesIO())
        response = Mock(status=status)
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        return response

    monkeypatch.setattr(mcp_url, "urlopen", respond)
    await async_probe_mcp_url(url, bearer_token=token)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "reason"),
    [
        (404, "MCP endpoint returned HTTP 404"),
        (500, "MCP endpoint returned HTTP 500"),
        (503, "MCP endpoint returned HTTP 503"),
        (TimeoutError("test-private-detail"), "MCP endpoint timed out"),
        (URLError(TimeoutError("test-private-detail")), "MCP endpoint timed out"),
        (URLError("test-private-detail"), "Could not connect to the MCP endpoint"),
        (
            ConnectionRefusedError("test-private-detail"),
            "Could not connect to the MCP endpoint",
        ),
    ],
)
async def test_probe_failure_is_retryable_and_sanitized(
    monkeypatch, failure, reason
) -> None:
    url = "http://127.0.0.1:8123/test-secret-path?token=test-secret-query"
    token = "test-ha-token"
    error = (
        HTTPError(url, failure, "test-private-detail", {}, BytesIO())
        if isinstance(failure, int)
        else failure
    )
    monkeypatch.setattr(mcp_url, "urlopen", Mock(side_effect=error))

    with pytest.raises(MCPProbeError, match=reason) as raised:
        await async_probe_mcp_url(url, bearer_token=token)

    details = "".join(traceback.format_exception(raised.value))
    for private in (url, "test-private-detail", token):
        assert private not in details
    if isinstance(error, HTTPError):
        assert error.closed
