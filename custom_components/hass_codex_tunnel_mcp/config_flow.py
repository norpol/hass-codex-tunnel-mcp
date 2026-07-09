"""Config flow for OpenAI Tunnel for HA-MCP."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_API_KEY
from homeassistant.core import callback

from .const import (
    CONF_CONTROL_PLANE_BASE_URL,
    CONF_CONTROL_PLANE_PATH,
    CONF_HA_MCP_URL,
    CONF_TUNNEL_ID,
    DEFAULT_CONTROL_PLANE_BASE_URL,
    DEFAULT_CONTROL_PLANE_PATH,
    DOMAIN,
    NAME,
)
from .validators import InputValidationError, normalize_user_input


def build_user_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    """Build the user/options schema."""
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(CONF_TUNNEL_ID, default=defaults.get(CONF_TUNNEL_ID, "")): str,
            vol.Required(CONF_API_KEY, default=defaults.get(CONF_API_KEY, "")): str,
            vol.Required(CONF_HA_MCP_URL, default=defaults.get(CONF_HA_MCP_URL, "")): str,
            vol.Optional(
                CONF_CONTROL_PLANE_BASE_URL,
                default=defaults.get(
                    CONF_CONTROL_PLANE_BASE_URL, DEFAULT_CONTROL_PLANE_BASE_URL
                ),
            ): str,
            vol.Optional(
                CONF_CONTROL_PLANE_PATH,
                default=defaults.get(CONF_CONTROL_PLANE_PATH, DEFAULT_CONTROL_PLANE_PATH),
            ): str,
        }
    )


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the integration config flow."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Handle setup from the UI."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                data = normalize_user_input(user_input)
            except InputValidationError as err:
                errors["base"] = err.code
            else:
                await self.async_set_unique_id(data[CONF_TUNNEL_ID])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=NAME, data=data)

        return self.async_show_form(
            step_id="user", data_schema=build_user_schema(user_input), errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Return the options flow."""
        return OptionsFlow(config_entry)


class OptionsFlow(config_entries.OptionsFlow):
    """Handle options updates."""

    def __init__(self, config_entry) -> None:
        self._config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        """Manage integration options."""
        errors: dict[str, str] = {}
        current = {**self._config_entry.data, **self._config_entry.options}
        if user_input is not None:
            try:
                data = normalize_user_input(user_input)
            except InputValidationError as err:
                errors["base"] = err.code
            else:
                return self.async_create_entry(title="", data=data)

        return self.async_show_form(
            step_id="init", data_schema=build_user_schema(current), errors=errors
        )
