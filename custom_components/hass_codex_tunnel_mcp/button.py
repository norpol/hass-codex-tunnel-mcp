"""Manual update controls on each integration device."""

from homeassistant.components.button import ButtonEntity
from homeassistant.exceptions import HomeAssistantError

from .binary import TunnelClientError
from .control import TunnelControlEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities(
        [
            TunnelUpdateButton(hass, entry, "check_releases"),
            TunnelUpdateButton(hass, entry, "install_release"),
        ]
    )


class TunnelUpdateButton(TunnelControlEntity, ButtonEntity):
    """Check releases or apply the operator's one-shot selection."""

    def __init__(self, hass, entry, action):
        super().__init__(hass, entry, action)
        self.action = action
        self._attr_icon = (
            "mdi:cloud-search" if action == "check_releases" else "mdi:download"
        )

    async def async_press(self):
        try:
            if self.action == "check_releases":
                await self.updater.async_check_for_update()
                return
            selected_version = self.updater.selected_version
            # Consume the choice even on failure, so a later click cannot
            # silently repeat an early-release override.
            self.updater.selected_version = None
            self.updater._notify()
            result = await self.updater.async_update_tunnel_client(
                self.runtime["tunnel"],
                self.runtime["entry_data"](),
                selected_version=selected_version,
            )
        except TunnelClientError as err:
            raise HomeAssistantError(str(err)) from err
        if result.candidate_asset is None:
            message = "No eligible newer release is available"
            if result.deferred_until:
                message += f"; next release eligibility: {result.deferred_until}"
            raise HomeAssistantError(message)
