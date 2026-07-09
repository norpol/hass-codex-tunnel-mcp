"""HA-MCP URL validation and probing helpers."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from http.client import HTTPResponse
import ipaddress
import socket
from urllib.error import HTTPError, URLError
from urllib.parse import ParseResult, urlsplit, urlunsplit
from urllib.request import Request, urlopen


class MCPUrlError(ValueError):
    """Raised when an HA-MCP URL is invalid or unreachable."""


@dataclass(frozen=True)
class MCPUrlAssessment:
    """Security assessment for a configured HA-MCP URL."""

    url: str
    redacted_url: str
    is_local_or_private: bool
    warning: str | None = None


_LOCAL_HOSTNAMES = {"localhost", "homeassistant", "homeassistant.local"}


def normalize_mcp_url(value: str) -> str:
    """Normalize and validate the HA-MCP server URL."""
    raw = value.strip()
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"}:
        raise MCPUrlError("invalid_mcp_url")
    if not parsed.hostname:
        raise MCPUrlError("invalid_mcp_url")
    if parsed.username or parsed.password:
        raise MCPUrlError("invalid_mcp_url")
    path = parsed.path.rstrip("/")
    if not path or path == "/":
        raise MCPUrlError("missing_mcp_path")
    if path == "/mcp":
        raise MCPUrlError("default_mcp_path")
    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            path,
            parsed.query,
            "",
        )
    )


def redact_mcp_url(value: str) -> str:
    """Redact the secret path of an HA-MCP URL."""
    try:
        parsed = urlsplit(value)
    except ValueError:
        return "**REDACTED**"
    if not parsed.scheme or not parsed.netloc:
        return "**REDACTED**"
    return urlunsplit((parsed.scheme, parsed.netloc, "/**REDACTED**", "", ""))


def assess_mcp_url(value: str) -> MCPUrlAssessment:
    """Return a security assessment for the HA-MCP URL."""
    normalized = normalize_mcp_url(value)
    parsed = urlsplit(normalized)
    private = _is_private_or_local(parsed)
    warning = None
    if not private:
        warning = "public_mcp_url"
    elif parsed.scheme == "https" and parsed.hostname not in _LOCAL_HOSTNAMES:
        warning = "https_mcp_url"
    return MCPUrlAssessment(
        url=normalized,
        redacted_url=redact_mcp_url(normalized),
        is_local_or_private=private,
        warning=warning,
    )


async def async_probe_mcp_url(value: str, timeout: float = 5.0) -> None:
    """Probe the configured HA-MCP URL for basic reachability."""
    await asyncio.to_thread(_probe_mcp_url, value, timeout)


def _probe_mcp_url(value: str, timeout: float) -> None:
    request = Request(value, method="GET", headers={"User-Agent": "hass-codex-tunnel-mcp"})
    try:
        with urlopen(request, timeout=timeout) as response:
            _validate_probe_response(response)
    except HTTPError as err:
        if err.code >= 500:
            raise MCPUrlError("mcp_probe_failed") from err
    except (TimeoutError, OSError, URLError) as err:
        raise MCPUrlError("mcp_probe_failed") from err


def _validate_probe_response(response: HTTPResponse) -> None:
    if response.status >= 500:
        raise MCPUrlError("mcp_probe_failed")


def _is_private_or_local(parsed: ParseResult) -> bool:
    hostname = parsed.hostname or ""
    lower = hostname.lower()
    if lower in _LOCAL_HOSTNAMES or lower.endswith(".local"):
        return True
    try:
        ip = ipaddress.ip_address(lower)
        return ip.is_private or ip.is_loopback
    except ValueError:
        pass

    try:
        infos = socket.getaddrinfo(hostname, parsed.port or _default_port(parsed.scheme))
    except OSError:
        return False
    for info in infos:
        address = info[4][0]
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return False
        if not (ip.is_private or ip.is_loopback):
            return False
    return bool(infos)


def _default_port(scheme: str) -> int:
    return 443 if scheme == "https" else 80
