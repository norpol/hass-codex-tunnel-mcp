from __future__ import annotations

from custom_components.hass_codex_tunnel_mcp.metrics import (
    derive_tunnel_endpoint_urls,
    iter_prometheus_samples,
    parse_tunnel_metrics,
    summarize_status_payload,
)


METRICS_TEXT = """
# HELP command_end_to_end_latency_milliseconds Latency in milliseconds.
# TYPE command_end_to_end_latency_milliseconds histogram
command_end_to_end_latency_milliseconds_bucket{channel="main",request_kind="call",request_method="tools/call",tunnel_service_status="200",le="500"} 3
command_end_to_end_latency_milliseconds_sum{channel="main",latency_type="enqueue_to_response",request_kind="call",request_method="initialize",tunnel_service_status="200"} 2541
command_end_to_end_latency_milliseconds_count{channel="main",latency_type="enqueue_to_response",request_kind="call",request_method="initialize",tunnel_service_status="200"} 10
command_end_to_end_latency_milliseconds_sum{channel="main",latency_type="enqueue_to_response",request_kind="call",request_method="resources/list",tunnel_service_status="200"} 500
command_end_to_end_latency_milliseconds_count{channel="main",latency_type="enqueue_to_response",request_kind="call",request_method="resources/list",tunnel_service_status="200"} 2
command_end_to_end_latency_milliseconds_sum{channel="main",latency_type="enqueue_to_response",request_kind="call",request_method="tools/call",tunnel_service_status="200"} 6905
command_end_to_end_latency_milliseconds_count{channel="main",latency_type="enqueue_to_response",request_kind="call",request_method="tools/call",tunnel_service_status="200"} 20
command_end_to_end_latency_milliseconds_count{channel="main",latency_type="enqueue_to_response",request_kind="call",request_method="tools/call",tunnel_service_status="500"} 1
command_end_to_end_latency_milliseconds_count{channel="harpoon",latency_type="enqueue_to_response",request_kind="call",request_method="tools/call",tunnel_service_status="200"} 99
command_end_to_end_latency_milliseconds_count{channel="main",latency_type="enqueue_to_response",request_kind="notification",request_method="tools/call",tunnel_service_status="200"} 99
http_client_request_duration_seconds_bucket{http_request_method="POST",http_response_status_code="200",http_route="/api/mcp",le="1"} 35
http_client_request_duration_seconds_sum{http_request_method="POST",http_response_status_code="200",http_route="/api/mcp"} 1.5008858740000004
http_client_request_duration_seconds_count{http_request_method="POST",http_response_status_code="200",http_route="/api/mcp"} 35
http_client_request_duration_seconds_sum{http_request_method="POST",http_response_status_code="202",http_route="/api/mcp"} 0.007575510000000001
http_client_request_duration_seconds_count{http_request_method="POST",http_response_status_code="202",http_route="/api/mcp"} 5
http_client_request_duration_seconds_sum{http_request_method="POST",http_response_status_code="500",http_route="/api/mcp"} 0.010024176000000001
http_client_request_duration_seconds_count{http_request_method="POST",http_response_status_code="500",http_route="/api/mcp"} 1
http_client_request_duration_seconds_count{http_request_method="POST",http_response_status_code="200",http_route="/not-mcp"} 99
process_cpu_seconds_total 35.26
promhttp_metric_handler_requests_total{code="200"} 6
target_info{service_name="unknown_service:tunnel-client"} 1
"""


def test_derive_tunnel_endpoint_urls_replaces_health_path() -> None:
    endpoints = derive_tunnel_endpoint_urls("http://127.0.0.1:43589/healthz?x=1")

    assert endpoints is not None
    assert endpoints.metrics_url == "http://127.0.0.1:43589/metrics"
    assert endpoints.status_url == "http://127.0.0.1:43589/api/status"
    assert endpoints.ui_url == "http://127.0.0.1:43589/ui"


def test_parse_tunnel_metrics_keeps_only_curated_series() -> None:
    summary = parse_tunnel_metrics(METRICS_TEXT)

    assert summary.mcp_request_count == 32
    assert summary.method_counts == {
        "initialize": 10,
        "tools/call": 20,
        "resources/list": 2,
    }
    assert summary.mcp_average_latency_ms == 310.812
    assert summary.method_average_latency_ms == {
        "initialize": 254.1,
        "tools/call": 345.25,
        "resources/list": 250.0,
    }
    assert summary.tunnel_error_count == 1
    assert summary.tunnel_error_statuses == {"500": 1}

    assert summary.upstream_request_count == 41
    assert summary.upstream_status_counts == {"200": 35, "202": 5, "500": 1}
    assert summary.upstream_average_duration_seconds == {
        "200": 0.042882,
        "202": 0.001515,
        "500": 0.010024,
    }
    assert summary.upstream_error_count == 1


def test_iter_prometheus_samples_ignores_buckets_and_go_internals() -> None:
    names = {sample.name for sample in iter_prometheus_samples(METRICS_TEXT)}

    assert "command_end_to_end_latency_milliseconds_bucket" not in names
    assert "http_client_request_duration_seconds_bucket" not in names
    assert "process_cpu_seconds_total" in names
    assert "promhttp_metric_handler_requests_total" in names

    summary = parse_tunnel_metrics(METRICS_TEXT)
    assert "process_cpu_seconds_total" not in summary.upstream_status_counts


def test_summarize_status_payload_exposes_only_safe_status_fields() -> None:
    summary = summarize_status_payload(
        {
            "version": "0.0.10+105e17a",
            "client_instance_id": "not-needed",
            "uptime_seconds": 1638,
            "control_plane_tunnel_id": "tunnel_6a5016a8384481918e662e3142ce6ba5",
            "mcp_server_url": "http://127.0.0.1:8123/api/mcp",
            "raw_http_logging_enabled": False,
            "channels": [
                {
                    "name": "main",
                    "enabled": True,
                    "server_kind": "external",
                    "transport_kind": "http-streamable",
                    "probe_status": "ok",
                    "details": [{"key": "address", "value": "secret-url"}],
                }
            ],
            "control_plane_route": {
                "route_mode": "direct",
                "proxy_source": "none",
                "target": "api.openai.com:443",
            },
            "mcp_routes": [
                {
                    "name": "main",
                    "route_mode": "direct",
                    "proxy_source": "none",
                    "target": "127.0.0.1:8123",
                }
            ],
            "tunnel_metadata": {
                "ID": "tunnel_6a5016a8384481918e662e3142ce6ba5",
                "Name": "home-assistant",
                "Description": "ha-tunnel",
            },
        }
    )

    attrs = summary.as_attributes()

    assert attrs["client_version"] == "0.0.10+105e17a"
    assert attrs["uptime_seconds"] == 1638
    assert attrs["tunnel_name"] == "home-assistant"
    assert attrs["tunnel_description"] == "ha-tunnel"
    assert attrs["raw_http_logging_enabled"] is False
    assert attrs["channels"] == [
        {
            "name": "main",
            "enabled": True,
            "server_kind": "external",
            "transport_kind": "http-streamable",
            "probe_status": "ok",
        }
    ]
    assert attrs["control_plane_route_mode"] == "direct"
    assert attrs["mcp_route_mode"] == "direct"
    assert "control_plane_tunnel_id" not in attrs
    assert "mcp_server_url" not in attrs
    assert "client_instance_id" not in attrs
