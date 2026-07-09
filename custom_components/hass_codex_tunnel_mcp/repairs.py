"""Repair issue helpers."""

from __future__ import annotations

from .const import DOMAIN


async def create_issue(hass, issue_id: str, translation_key: str, placeholders=None) -> None:
    """Create a Home Assistant repair issue if the repairs API is available."""
    try:
        from homeassistant.helpers import issue_registry as ir
    except Exception:
        return

    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key=translation_key,
        translation_placeholders=placeholders or {},
    )


async def delete_issue(hass, issue_id: str) -> None:
    """Delete a Home Assistant repair issue if the repairs API is available."""
    try:
        from homeassistant.helpers import issue_registry as ir
    except Exception:
        return

    ir.async_delete_issue(hass, DOMAIN, issue_id)
