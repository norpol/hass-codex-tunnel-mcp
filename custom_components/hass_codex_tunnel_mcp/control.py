"""Shared runtime binding for entry-specific update controls."""

from homeassistant.const import EntityCategory
from homeassistant.core import callback
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, NAME

LATEST_ELIGIBLE = "Latest eligible (8-day hold)"


class TunnelControlEntity(Entity):
    """Follow updater notifications without polling or contacting a live tunnel."""

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, hass, entry, key):
        self.runtime = hass.data[DOMAIN][entry.entry_id]
        self.updater = self.runtime["updater"]
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": NAME,
            "manufacturer": "OpenAI",
        }

    async def async_added_to_hass(self):
        await super().async_added_to_hass()

        @callback
        def update():
            self.async_write_ha_state()

        listeners = self.runtime["listeners"]
        listeners.append(update)
        self.async_on_remove(lambda: listeners.remove(update))
