from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from custom_components.hass_codex_tunnel_mcp.health import summarize_health_payload
from custom_components.hass_codex_tunnel_mcp.metrics import (
    TunnelMetricsSnapshot,
    derive_tunnel_endpoint_urls,
    parse_tunnel_metrics,
)

pytestmark = pytest.mark.filterwarnings(
    "ignore:Inheritance class HomeAssistantApplication from web.Application is discouraged:DeprecationWarning"
)


def test_health_whitelists_observations_and_preserves_missing_evidence():
    health = summarize_health_payload(
        {
            "schema_version": 1,
            "live": True,
            "ready": False,
            "runtime": {"instance_id": "secret"},
            "components": {
                "queue": {
                    "status": "ok",
                    "state": "available",
                    "limited": False,
                    "observed_at": "2026-10-25T10:00:00Z",
                    "details": {
                        "depth": 0,
                        "capacity": 20,
                        "utilization": 0,
                        "raw": "secret",
                    },
                },
                "mcp": {
                    "status": "unknown",
                    "state": "not_observed",
                    "reason_code": "same_child_evidence_unavailable",
                    "details": {
                        "transport": "http-streamable",
                        "evidence": "unsupported_transport",
                        "initialize": {"ok": False, "server_name": "secret"},
                        "tools_list": {
                            "ok": False,
                            "retained_count": 0,
                            "tool_names": ["secret"],
                        },
                        "startup_probe": {"state": "succeeded", "raw_error": "secret"},
                    },
                },
                "future": {"secret": "secret"},
            },
        }
    )
    assert health.ready is False
    assert health.live is True
    assert health.components["queue"]["details"] == {
        "depth": 0,
        "capacity": 20,
        "utilization": 0,
    }
    mcp = health.components["mcp"]["details"]
    assert mcp["startup_probe"] == {"state": "succeeded"}
    assert mcp["tools_list"] == {"ok": False}
    assert "secret" not in json.dumps(health.components)
    assert "future" not in health.components


def test_modern_mcp_discovery_and_partial_catalog_keep_limits():
    health = summarize_health_payload(
        {
            "schema_version": 1,
            "components": {
                "mcp": {
                    "limited": True,
                    "details": {
                        "server_discover": {
                            "ok": True,
                            "limited": False,
                            "supported_versions": ["2026-07-28"],
                            "capability_names": ["tools", "tools"],
                        },
                        "tools_list": {
                            "ok": True,
                            "retained_count": 2,
                            "complete": False,
                            "partial": True,
                            "limited": True,
                            "tool_names": ["secret"],
                        },
                    },
                }
            },
            "truncated": True,
        }
    )
    mcp = health.components["mcp"]
    assert mcp["limited"] and health.truncated
    assert mcp["details"]["server_discover"]["supported_versions"] == ["2026-07-28"]
    assert mcp["details"]["tools_list"] == {
        "ok": True,
        "retained_count": 2,
        "complete": False,
        "partial": True,
        "limited": True,
    }


@pytest.mark.parametrize(
    "payload", [None, [], {}, {"schema_version": 2}, {"schema_version": True}]
)
def test_unsupported_schema_is_not_a_success(payload):
    with pytest.raises(ValueError, match="schema"):
        summarize_health_payload(payload)


def test_invalid_and_nonfinite_values_do_not_become_measurements():
    health = summarize_health_payload(
        {
            "schema_version": 1,
            "ready": "true",
            "components": {
                "queue": {
                    "state": "https://secret",
                    "observed_at": "secret",
                    "details": {
                        "depth": True,
                        "capacity": -1,
                        "utilization": float("nan"),
                        "backpressure_seconds": float("inf"),
                    },
                },
                "mcp": {"details": []},
            },
        }
    )
    assert health.ready is None
    assert health.components["queue"] == {"details": {}}
    assert health.components["mcp"] == {"details": {}}


def test_health_sensor_availability_is_independent_of_metrics():
    from custom_components.hass_codex_tunnel_mcp.sensor import (
        HEALTH_SENSORS,
        TunnelHealthSensor,
    )

    health = summarize_health_payload(
        {
            "schema_version": 1,
            "components": {
                "queue": {
                    "state": "available",
                    "details": {"depth": 0, "capacity": 20},
                },
            },
        }
    )
    snapshot = TunnelMetricsSnapshot(
        endpoints=derive_tunnel_endpoint_urls("http://127.0.0.1:1234"),
        metrics_error="metrics unavailable",
        health=health,
    )
    coordinator = SimpleNamespace(data=snapshot, last_update_success=True)
    entry = SimpleNamespace(entry_id="test")
    entities = {
        d.key: TunnelHealthSensor(coordinator, entry, d) for d in HEALTH_SENSORS
    }
    queue = entities["tunnel_queue_depth"]
    assert queue.available and queue.native_value == 0
    assert queue.extra_state_attributes["details"]["capacity"] == 20
    assert not entities["tunnel_active_requests"].available
    coordinator.data = TunnelMetricsSnapshot(
        endpoints=snapshot.endpoints, health_error="404"
    )
    assert not queue.available
    assert queue.native_value is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "health_response",
    [
        RuntimeError("HTTP 404"),
        {"schema_version": 2},
        {"schema_version": 1, "ready": True},
    ],
)
async def test_health_endpoint_failure_does_not_break_other_sensors(
    monkeypatch, health_response
):
    from custom_components.hass_codex_tunnel_mcp import sensor
    from custom_components.hass_codex_tunnel_mcp.sensor import TunnelMetricsCoordinator

    async def text(session, url):
        return ""

    async def json_response(session, url):
        if "/health?" in url:
            if isinstance(health_response, Exception):
                raise health_response
            return health_response
        return {"version": "0.0.18"}

    async def exists(session, url):
        return True

    monkeypatch.setattr(sensor, "async_get_clientsession", lambda hass: None)
    monkeypatch.setattr(sensor, "_fetch_text", text)
    monkeypatch.setattr(sensor, "_fetch_json", json_response)
    monkeypatch.setattr(sensor, "_endpoint_exists", exists)
    coordinator = SimpleNamespace(
        hass=None,
        runtime={
            "tunnel": SimpleNamespace(
                status=SimpleNamespace(health_url="http://127.0.0.1:1234")
            )
        },
    )
    snapshot = await TunnelMetricsCoordinator._async_update_data(coordinator)
    assert snapshot.metrics_error is None
    assert snapshot.status.version == "0.0.18"
    assert snapshot.local_ui_available
    assert bool(snapshot.health_error) == (
        health_response != {"schema_version": 1, "ready": True}
    )


def test_modern_discovery_is_counted_in_mcp_metrics():
    metrics = parse_tunnel_metrics(
        "command_end_to_end_latency_milliseconds_count"
        '{channel="main",request_method="server/discover",tunnel_service_status="200"} 3'
    )
    assert metrics.method_counts == {"server/discover": 3}


def test_health_endpoint_derivation():
    endpoints = derive_tunnel_endpoint_urls("http://127.0.0.1:1234/healthz?secret=x")
    assert endpoints.ready_url == "http://127.0.0.1:1234/readyz"
    assert endpoints.health_details_url == "http://127.0.0.1:1234/health?details=true"
