"""Sensors for OpenAI Tunnel for HA-MCP."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, NAME, TUNNEL_CLIENT_VERSION


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up status sensors."""
    async_add_entities(
        [
            TunnelProcessSensor(hass, entry),
            TunnelHealthySensor(hass, entry),
            TunnelVersionSensor(hass, entry),
        ]
    )


class BaseTunnelSensor(SensorEntity):
    """Base class for integration status sensors."""

    _attr_has_entity_name = True

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, key: str, name: str) -> None:
        self.hass = hass
        self.entry = entry
        self._key = key
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._remove_listener = None

    @property
    def device_info(self):
        """Return integration device info."""
        return {
            "identifiers": {(DOMAIN, self.entry.entry_id)},
            "name": NAME,
            "manufacturer": "OpenAI",
        }

    @property
    def runtime(self):
        """Return runtime state for this entry."""
        return self.hass.data[DOMAIN][self.entry.entry_id]

    async def async_added_to_hass(self) -> None:
        """Register for runtime updates."""
        @callback
        def _update() -> None:
            self.async_write_ha_state()

        self.runtime["listeners"].append(_update)
        self._remove_listener = _update

    async def async_will_remove_from_hass(self) -> None:
        """Unregister runtime update callback."""
        if self._remove_listener in self.runtime["listeners"]:
            self.runtime["listeners"].remove(self._remove_listener)


class TunnelProcessSensor(BaseTunnelSensor):
    """Tunnel process state sensor."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, entry, "tunnel_process_state", "Tunnel process state")

    @property
    def native_value(self):
        """Return tunnel process state."""
        return self.runtime["tunnel"].status.state

    @property
    def extra_state_attributes(self):
        """Return tunnel process attributes."""
        status = self.runtime["tunnel"].status
        return {
            "health_url": status.health_url,
            "returncode": status.returncode,
            "last_error": status.last_error,
        }


class TunnelHealthySensor(BaseTunnelSensor):
    """Tunnel readiness sensor."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, entry, "tunnel_ready", "Tunnel ready")

    @property
    def native_value(self):
        """Return readiness state."""
        return "ready" if self.runtime["tunnel"].status.healthy else "not_ready"


class TunnelVersionSensor(BaseTunnelSensor):
    """Installed tunnel-client version sensor."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, entry, "tunnel_client_version", "Tunnel-client version")

    @property
    def native_value(self):
        """Return pinned tunnel-client version."""
        return TUNNEL_CLIENT_VERSION
