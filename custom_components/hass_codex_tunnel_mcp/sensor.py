"""Sensors for OpenAI Tunnel for HA-MCP."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator

from .const import DOMAIN, NAME
from .metrics import (
    TunnelClientStatusSummary,
    TunnelMetricsSummary,
    TunnelMetricsSnapshot,
    derive_tunnel_endpoint_urls,
    parse_tunnel_metrics,
    summarize_status_payload,
)

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(seconds=60)
FETCH_TIMEOUT = 5


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up status sensors."""
    runtime = hass.data[DOMAIN][entry.entry_id]
    coordinator = runtime.get("metrics_coordinator")
    if coordinator is None:
        coordinator = TunnelMetricsCoordinator(hass, entry)
        runtime["metrics_coordinator"] = coordinator
    if "metrics_refresh_listener" not in runtime:
        @callback
        def _refresh_metrics() -> None:
            hass.async_create_task(coordinator.async_request_refresh())

        runtime["listeners"].append(_refresh_metrics)
        runtime["metrics_refresh_listener"] = _refresh_metrics
    await coordinator.async_config_entry_first_refresh()

    async_add_entities(
        [
            TunnelProcessSensor(hass, entry),
            TunnelHealthySensor(hass, entry),
            TunnelVersionSensor(hass, entry),
            TunnelMCPRequestsSensor(coordinator, entry),
            TunnelMCPLatencySensor(coordinator, entry),
            TunnelMCPErrorsSensor(coordinator, entry),
            TunnelUpstreamRequestsSensor(coordinator, entry),
        ]
    )


class TunnelMetricsCoordinator(DataUpdateCoordinator):
    """Fetch local tunnel-client metrics and status."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.entry_id}_metrics",
            update_interval=SCAN_INTERVAL,
        )
        self.entry = entry

    @property
    def runtime(self):
        """Return runtime state for this entry."""
        return self.hass.data[DOMAIN][self.entry.entry_id]

    async def _async_update_data(self) -> TunnelMetricsSnapshot:
        status = self.runtime["tunnel"].status
        endpoints = derive_tunnel_endpoint_urls(status.health_url)
        if endpoints is None:
            return TunnelMetricsSnapshot(
                metrics_error="Tunnel health URL is not available",
                status_error="Tunnel health URL is not available",
            )

        session = async_get_clientsession(self.hass)
        metrics_error = None
        status_error = None
        local_ui_available = False
        metrics = None
        status_summary = None

        try:
            metrics_text = await _fetch_text(session, endpoints.metrics_url)
            metrics = parse_tunnel_metrics(metrics_text)
        except Exception as err:  # noqa: BLE001
            metrics_error = str(err)

        try:
            status_payload = await _fetch_json(session, endpoints.status_url)
            status_summary = summarize_status_payload(status_payload)
        except Exception as err:  # noqa: BLE001
            status_error = str(err)

        try:
            local_ui_available = await _endpoint_exists(session, endpoints.ui_url)
        except Exception:  # noqa: BLE001
            local_ui_available = False

        return TunnelMetricsSnapshot(
            endpoints=endpoints,
            metrics=metrics or TunnelMetricsSummary(),
            status=status_summary or TunnelClientStatusSummary(),
            local_ui_available=local_ui_available,
            metrics_error=metrics_error,
            status_error=status_error,
        )


async def _fetch_text(session, url: str) -> str:
    async with asyncio.timeout(FETCH_TIMEOUT):
        async with session.get(url) as response:
            if response.status != 200:
                raise RuntimeError(f"{url} returned HTTP {response.status}")
            return await response.text()


async def _fetch_json(session, url: str) -> Any:
    async with asyncio.timeout(FETCH_TIMEOUT):
        async with session.get(url) as response:
            if response.status != 200:
                raise RuntimeError(f"{url} returned HTTP {response.status}")
            return await response.json(content_type=None)


async def _endpoint_exists(session, url: str) -> bool:
    async with asyncio.timeout(FETCH_TIMEOUT):
        async with session.head(url) as response:
            return response.status == 200


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
        """Return active tunnel-client version."""
        return self.runtime["tunnel"].status.version

    @property
    def extra_state_attributes(self):
        """Return updater status attributes."""
        updater = self.runtime.get("updater")
        if updater is None:
            return {}
        return _drop_empty_attributes(updater.state.as_attributes())


class BaseTunnelMetricsSensor(CoordinatorEntity, SensorEntity):
    """Base class for coordinator-backed tunnel metrics sensors."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: TunnelMetricsCoordinator,
        entry: ConfigEntry,
        key: str,
        name: str,
    ) -> None:
        super().__init__(coordinator)
        self.entry = entry
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_{key}"

    @property
    def device_info(self):
        """Return integration device info."""
        return {
            "identifiers": {(DOMAIN, self.entry.entry_id)},
            "name": NAME,
            "manufacturer": "OpenAI",
        }

    @property
    def available(self) -> bool:
        """Return true when metrics were fetched successfully."""
        data = self.coordinator.data
        return (
            super().available
            and data is not None
            and data.endpoints is not None
            and data.metrics_error is None
        )

    @property
    def snapshot(self) -> TunnelMetricsSnapshot:
        """Return the latest metrics snapshot."""
        return self.coordinator.data or TunnelMetricsSnapshot()

    def _common_attributes(self) -> dict[str, Any]:
        snapshot = self.snapshot
        attrs = {
            "local_ui_available": snapshot.local_ui_available,
            "metrics_error": snapshot.metrics_error,
            "status_error": snapshot.status_error,
        }
        attrs.update(snapshot.status.as_attributes())
        return _drop_empty_attributes(attrs)


class TunnelMCPRequestsSensor(BaseTunnelMetricsSensor):
    """Selected MCP command request counter."""

    _attr_icon = "mdi:counter"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(
        self, coordinator: TunnelMetricsCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator, entry, "tunnel_mcp_requests", "Tunnel MCP requests")

    @property
    def native_value(self):
        """Return total selected MCP command requests."""
        return self.snapshot.metrics.mcp_request_count

    @property
    def extra_state_attributes(self):
        """Return request breakdown attributes."""
        attrs = self._common_attributes()
        attrs["method_counts"] = self.snapshot.metrics.method_counts
        return _drop_empty_attributes(attrs)


class TunnelMCPLatencySensor(BaseTunnelMetricsSensor):
    """Selected MCP command average latency sensor."""

    _attr_icon = "mdi:timer-outline"
    _attr_native_unit_of_measurement = UnitOfTime.MILLISECONDS
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self, coordinator: TunnelMetricsCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(
            coordinator,
            entry,
            "tunnel_mcp_average_latency",
            "Tunnel MCP average latency",
        )

    @property
    def native_value(self):
        """Return average selected MCP command latency in milliseconds."""
        return self.snapshot.metrics.mcp_average_latency_ms

    @property
    def extra_state_attributes(self):
        """Return latency breakdown attributes."""
        attrs = self._common_attributes()
        attrs["method_average_latency_ms"] = (
            self.snapshot.metrics.method_average_latency_ms
        )
        attrs["method_counts"] = self.snapshot.metrics.method_counts
        return _drop_empty_attributes(attrs)


class TunnelMCPErrorsSensor(BaseTunnelMetricsSensor):
    """Tunnel and upstream error counter."""

    _attr_icon = "mdi:alert-circle-outline"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(
        self, coordinator: TunnelMetricsCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator, entry, "tunnel_mcp_errors", "Tunnel MCP errors")

    @property
    def native_value(self):
        """Return total selected MCP/tunnel error count."""
        metrics = self.snapshot.metrics
        return metrics.tunnel_error_count + metrics.upstream_error_count

    @property
    def extra_state_attributes(self):
        """Return error breakdown attributes."""
        metrics = self.snapshot.metrics
        attrs = self._common_attributes()
        attrs.update(
            {
                "tunnel_error_count": metrics.tunnel_error_count,
                "tunnel_error_statuses": metrics.tunnel_error_statuses,
                "upstream_error_count": metrics.upstream_error_count,
                "upstream_status_counts": metrics.upstream_status_counts,
            }
        )
        return _drop_empty_attributes(attrs)


class TunnelUpstreamRequestsSensor(BaseTunnelMetricsSensor):
    """Upstream HA-MCP HTTP request counter."""

    _attr_icon = "mdi:transit-connection-variant"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(
        self, coordinator: TunnelMetricsCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(
            coordinator,
            entry,
            "tunnel_upstream_requests",
            "Tunnel upstream requests",
        )

    @property
    def native_value(self):
        """Return total upstream /api/mcp request count."""
        return self.snapshot.metrics.upstream_request_count

    @property
    def extra_state_attributes(self):
        """Return upstream request breakdown attributes."""
        metrics = self.snapshot.metrics
        attrs = self._common_attributes()
        attrs.update(
            {
                "upstream_status_counts": metrics.upstream_status_counts,
                "upstream_average_duration_seconds": (
                    metrics.upstream_average_duration_seconds
                ),
            }
        )
        return _drop_empty_attributes(attrs)


def _drop_empty_attributes(attributes: dict[str, Any]) -> dict[str, Any]:
    """Remove noisy empty attributes while keeping false and zero values."""
    return {
        key: value
        for key, value in attributes.items()
        if value is not None and value != {} and value != []
    }
