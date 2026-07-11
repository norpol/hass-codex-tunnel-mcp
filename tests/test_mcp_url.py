from __future__ import annotations

from custom_components.hass_codex_tunnel_mcp.mcp_url import (
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
