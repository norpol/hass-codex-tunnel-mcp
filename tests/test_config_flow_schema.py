from __future__ import annotations

import pytest

from custom_components.hass_codex_tunnel_mcp.const import (
    CONF_API_KEY,
    CONF_HA_MCP_BEARER_TOKEN,
)

selector = pytest.importorskip("homeassistant.helpers.selector")
config_flow = pytest.importorskip("custom_components.hass_codex_tunnel_mcp.config_flow")


def _schema_validator_for(field_name: str) -> object:
    schema = config_flow.build_user_schema()
    for marker, validator in schema.schema.items():
        if getattr(marker, "schema", marker) == field_name:
            return validator
    raise AssertionError(f"{field_name} is missing from the config flow schema")


@pytest.mark.parametrize("field_name", [CONF_API_KEY, CONF_HA_MCP_BEARER_TOKEN])
def test_sensitive_fields_use_password_selector(field_name: str) -> None:
    validator = _schema_validator_for(field_name)

    assert isinstance(validator, selector.TextSelector)
    assert validator.config["type"] == selector.TextSelectorType.PASSWORD.value
