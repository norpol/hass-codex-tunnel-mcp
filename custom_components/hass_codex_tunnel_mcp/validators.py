"""Pure validation helpers for config/options input."""

from __future__ import annotations

import re
from typing import Any

from .const import (
    CONF_API_KEY,
    CONF_CONTROL_PLANE_BASE_URL,
    CONF_CONTROL_PLANE_PATH,
    CONF_TUNNEL_ID,
    TUNNEL_ID_RE,
)


class InputValidationError(ValueError):
    """Raised when config input is invalid."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def validate_tunnel_id(value: str) -> str:
    """Validate a control-plane tunnel id."""
    normalized = value.strip()
    if not re.fullmatch(TUNNEL_ID_RE, normalized):
        raise InputValidationError("invalid_tunnel_id")
    return normalized


def normalize_optional_url(value: str | None) -> str:
    """Normalize optional URL/path fields."""
    return (value or "").strip()


def normalize_user_input(user_input: dict[str, Any]) -> dict[str, Any]:
    """Normalize and validate flow input."""
    api_key = str(user_input[CONF_API_KEY]).strip()
    if not api_key:
        raise InputValidationError("missing_api_key")
    return {
        CONF_TUNNEL_ID: validate_tunnel_id(str(user_input[CONF_TUNNEL_ID])),
        CONF_API_KEY: api_key,
        CONF_CONTROL_PLANE_BASE_URL: normalize_optional_url(
            user_input.get(CONF_CONTROL_PLANE_BASE_URL)
        ),
        CONF_CONTROL_PLANE_PATH: normalize_optional_url(
            user_input.get(CONF_CONTROL_PLANE_PATH)
        ),
    }
