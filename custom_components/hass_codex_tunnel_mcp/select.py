"""One-shot manual release selection; never changes automatic update policy."""

from homeassistant.components.select import SelectEntity
from homeassistant.exceptions import HomeAssistantError

from .control import LATEST_ELIGIBLE, TunnelControlEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([TunnelReleaseSelect(hass, entry)])


class TunnelReleaseSelect(TunnelControlEntity, SelectEntity):
    """Select a version explicitly to override the release age hold once."""

    _attr_icon = "mdi:source-branch"

    def __init__(self, hass, entry):
        super().__init__(hass, entry, "release_override")

    @property
    def options(self):
        return [LATEST_ELIGIBLE, *self.updater.available_versions]

    @property
    def current_option(self):
        return self.updater.selected_version or LATEST_ELIGIBLE

    async def async_select_option(self, option):
        if option not in self.options:
            raise HomeAssistantError(
                "Check releases again before selecting this version"
            )
        self.updater.selected_version = None if option == LATEST_ELIGIBLE else option
        self.updater._notify()
