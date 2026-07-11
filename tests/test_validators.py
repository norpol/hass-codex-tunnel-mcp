from __future__ import annotations

import pytest

from custom_components.hass_codex_tunnel_mcp.const import (
    CONF_AUTO_UPDATE_TUNNEL_CLIENT,
    CONF_API_KEY,
    CONF_CONTROL_PLANE_BASE_URL,
    CONF_CONTROL_PLANE_PATH,
    CONF_HA_MCP_BEARER_TOKEN,
    CONF_HA_MCP_URL,
    CONF_TUNNEL_ID,
)
from custom_components.hass_codex_tunnel_mcp.validators import (
    InputValidationError,
    normalize_user_input,
)


def test_normalize_user_input_accepts_valid_tunnel_id() -> None:
    data = normalize_user_input(
        {
            CONF_TUNNEL_ID: " tunnel_0123456789abcdef0123456789abcdef ",
            CONF_API_KEY: " key ",
            CONF_HA_MCP_URL: " http://127.0.0.1:9584/private_secret/ ",
            CONF_HA_MCP_BEARER_TOKEN: " ha-token ",
            CONF_CONTROL_PLANE_BASE_URL: " https://example.test ",
            CONF_CONTROL_PLANE_PATH: " /v1 ",
            CONF_AUTO_UPDATE_TUNNEL_CLIENT: False,
        }
    )

    assert data == {
        CONF_TUNNEL_ID: "tunnel_0123456789abcdef0123456789abcdef",
        CONF_API_KEY: "key",
        CONF_HA_MCP_URL: "http://127.0.0.1:9584/private_secret",
        CONF_HA_MCP_BEARER_TOKEN: "ha-token",
        CONF_CONTROL_PLANE_BASE_URL: "https://example.test",
        CONF_CONTROL_PLANE_PATH: "/v1",
        CONF_AUTO_UPDATE_TUNNEL_CLIENT: False,
    }


@pytest.mark.parametrize(
    "tunnel_id",
    [
        "tunnel_0123456789ABCDEF0123456789abcdef",
        "tun_0123456789abcdef0123456789abcdef",
        "tunnel_0123456789abcdef",
    ],
)
def test_normalize_user_input_rejects_invalid_tunnel_id(tunnel_id: str) -> None:
    with pytest.raises(InputValidationError) as err:
        normalize_user_input(
            {
                CONF_TUNNEL_ID: tunnel_id,
                CONF_API_KEY: "key",
                CONF_HA_MCP_URL: "http://127.0.0.1:9584/private_secret",
            }
        )

    assert err.value.code == "invalid_tunnel_id"


def test_normalize_user_input_rejects_empty_api_key() -> None:
    with pytest.raises(InputValidationError) as err:
        normalize_user_input(
            {
                CONF_TUNNEL_ID: "tunnel_0123456789abcdef0123456789abcdef",
                CONF_API_KEY: " ",
                CONF_HA_MCP_URL: "http://127.0.0.1:9584/private_secret",
            }
        )

    assert err.value.code == "missing_api_key"


@pytest.mark.parametrize(
    ("url", "code"),
    [
        ("not-a-url", "invalid_mcp_url"),
        ("http://127.0.0.1:9584", "missing_mcp_path"),
        ("http://127.0.0.1:9584/mcp", "default_mcp_path"),
    ],
)
def test_normalize_user_input_rejects_invalid_mcp_url(url: str, code: str) -> None:
    with pytest.raises(InputValidationError) as err:
        normalize_user_input(
            {
                CONF_TUNNEL_ID: "tunnel_0123456789abcdef0123456789abcdef",
                CONF_API_KEY: "key",
                CONF_HA_MCP_URL: url,
            }
        )

    assert err.value.code == code
