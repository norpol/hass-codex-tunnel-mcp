"""Curated tunnel-client metrics and status parsing."""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any
from urllib.parse import urlsplit, urlunsplit

COMMAND_COUNT = "command_end_to_end_latency_milliseconds_count"
COMMAND_SUM = "command_end_to_end_latency_milliseconds_sum"
HTTP_COUNT = "http_client_request_duration_seconds_count"
HTTP_SUM = "http_client_request_duration_seconds_sum"

MCP_METHODS = (
    "initialize",
    "tools/list",
    "tools/call",
    "resources/list",
    "resources/read",
)
SUCCESS_TUNNEL_STATUS = "200"
SUCCESS_UPSTREAM_STATUSES = {"200", "202"}


@dataclass(frozen=True)
class TunnelEndpointUrls:
    """Local tunnel-client endpoints derived from the health URL."""

    metrics_url: str
    status_url: str
    ui_url: str


@dataclass(frozen=True)
class PrometheusSample:
    """A single Prometheus text exposition sample."""

    name: str
    labels: dict[str, str]
    value: float


@dataclass(frozen=True)
class TunnelMetricsSummary:
    """Curated, low-cardinality tunnel metrics."""

    mcp_request_count: int = 0
    mcp_average_latency_ms: float | None = None
    method_counts: dict[str, int] = field(default_factory=dict)
    method_average_latency_ms: dict[str, float] = field(default_factory=dict)
    tunnel_error_count: int = 0
    tunnel_error_statuses: dict[str, int] = field(default_factory=dict)
    upstream_request_count: int = 0
    upstream_status_counts: dict[str, int] = field(default_factory=dict)
    upstream_average_duration_seconds: dict[str, float] = field(default_factory=dict)
    upstream_error_count: int = 0


@dataclass(frozen=True)
class TunnelClientStatusSummary:
    """Safe read-only tunnel-client status details."""

    version: str | None = None
    uptime_seconds: int | None = None
    tunnel_name: str | None = None
    tunnel_description: str | None = None
    raw_http_logging_enabled: bool | None = None
    channels: list[dict[str, Any]] = field(default_factory=list)
    control_plane_route_mode: str | None = None
    control_plane_proxy_source: str | None = None
    mcp_route_mode: str | None = None
    mcp_proxy_source: str | None = None

    def as_attributes(self) -> dict[str, Any]:
        """Return Home Assistant-safe attributes."""
        return {
            "client_version": self.version,
            "uptime_seconds": self.uptime_seconds,
            "tunnel_name": self.tunnel_name,
            "tunnel_description": self.tunnel_description,
            "raw_http_logging_enabled": self.raw_http_logging_enabled,
            "channels": self.channels,
            "control_plane_route_mode": self.control_plane_route_mode,
            "control_plane_proxy_source": self.control_plane_proxy_source,
            "mcp_route_mode": self.mcp_route_mode,
            "mcp_proxy_source": self.mcp_proxy_source,
        }


@dataclass(frozen=True)
class TunnelMetricsSnapshot:
    """Current metrics and status snapshot from tunnel-client."""

    endpoints: TunnelEndpointUrls | None = None
    metrics: TunnelMetricsSummary = field(default_factory=TunnelMetricsSummary)
    status: TunnelClientStatusSummary = field(default_factory=TunnelClientStatusSummary)
    local_ui_available: bool = False
    metrics_error: str | None = None
    status_error: str | None = None

    @property
    def available(self) -> bool:
        """Return true if at least metrics or status fetched successfully."""
        return self.endpoints is not None and (
            self.metrics_error is None or self.status_error is None
        )


def derive_tunnel_endpoint_urls(health_url: str | None) -> TunnelEndpointUrls | None:
    """Build local tunnel-client endpoint URLs from a health URL."""
    if not health_url:
        return None
    try:
        parsed = urlsplit(health_url)
    except ValueError:
        return None
    if not parsed.scheme or not parsed.netloc:
        return None

    def with_path(path: str) -> str:
        return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))

    return TunnelEndpointUrls(
        metrics_url=with_path("/metrics"),
        status_url=with_path("/api/status"),
        ui_url=with_path("/ui"),
    )


def parse_tunnel_metrics(text: str) -> TunnelMetricsSummary:
    """Parse tunnel-client Prometheus text into curated metrics."""
    method_counts: dict[str, int] = {}
    method_sums: dict[str, float] = {}
    tunnel_error_statuses: dict[str, int] = {}
    upstream_counts: dict[str, int] = {}
    upstream_sums: dict[str, float] = {}

    for sample in iter_prometheus_samples(text):
        if sample.name in {COMMAND_COUNT, COMMAND_SUM}:
            _collect_command_sample(
                sample,
                method_counts=method_counts,
                method_sums=method_sums,
                tunnel_error_statuses=tunnel_error_statuses,
            )
        elif sample.name in {HTTP_COUNT, HTTP_SUM}:
            _collect_upstream_sample(
                sample,
                upstream_counts=upstream_counts,
                upstream_sums=upstream_sums,
            )

    method_average_latency_ms = {
        method: _rounded(method_sums[method] / count)
        for method, count in method_counts.items()
        if count > 0 and method in method_sums
    }
    mcp_request_count = sum(method_counts.values())
    mcp_average_latency_ms = (
        _rounded(sum(method_sums.values()) / mcp_request_count)
        if mcp_request_count
        else None
    )

    upstream_average_duration_seconds = {
        status: _rounded(upstream_sums[status] / count, 6)
        for status, count in upstream_counts.items()
        if count > 0 and status in upstream_sums
    }

    return TunnelMetricsSummary(
        mcp_request_count=mcp_request_count,
        mcp_average_latency_ms=mcp_average_latency_ms,
        method_counts=_ordered_known_methods(method_counts),
        method_average_latency_ms=_ordered_known_methods(method_average_latency_ms),
        tunnel_error_count=sum(tunnel_error_statuses.values()),
        tunnel_error_statuses=dict(sorted(tunnel_error_statuses.items())),
        upstream_request_count=sum(upstream_counts.values()),
        upstream_status_counts=dict(sorted(upstream_counts.items())),
        upstream_average_duration_seconds=dict(
            sorted(upstream_average_duration_seconds.items())
        ),
        upstream_error_count=sum(
            count
            for status, count in upstream_counts.items()
            if status not in SUCCESS_UPSTREAM_STATUSES
        ),
    )


def iter_prometheus_samples(text: str):
    """Yield finite Prometheus samples from text exposition."""
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        sample = _parse_prometheus_line(line)
        if sample is not None:
            yield sample


def summarize_status_payload(payload: Any) -> TunnelClientStatusSummary:
    """Summarize safe fields from tunnel-client /api/status JSON."""
    if not isinstance(payload, dict):
        return TunnelClientStatusSummary()

    tunnel_metadata = payload.get("tunnel_metadata")
    if not isinstance(tunnel_metadata, dict):
        tunnel_metadata = {}

    control_route = payload.get("control_plane_route")
    if not isinstance(control_route, dict):
        control_route = {}

    mcp_route = _find_named_route(payload.get("mcp_routes"), "main")

    return TunnelClientStatusSummary(
        version=_string_or_none(payload.get("version")),
        uptime_seconds=_int_or_none(payload.get("uptime_seconds")),
        tunnel_name=_string_or_none(
            tunnel_metadata.get("Name") or tunnel_metadata.get("name")
        ),
        tunnel_description=_string_or_none(
            tunnel_metadata.get("Description") or tunnel_metadata.get("description")
        ),
        raw_http_logging_enabled=_bool_or_none(payload.get("raw_http_logging_enabled")),
        channels=_summarize_channels(payload.get("channels")),
        control_plane_route_mode=_string_or_none(control_route.get("route_mode")),
        control_plane_proxy_source=_string_or_none(control_route.get("proxy_source")),
        mcp_route_mode=_string_or_none(mcp_route.get("route_mode")),
        mcp_proxy_source=_string_or_none(mcp_route.get("proxy_source")),
    )


def _collect_command_sample(
    sample: PrometheusSample,
    *,
    method_counts: dict[str, int],
    method_sums: dict[str, float],
    tunnel_error_statuses: dict[str, int],
) -> None:
    labels = sample.labels
    if labels.get("request_kind", "call") != "call":
        return
    if labels.get("channel", "main") != "main":
        return
    if labels.get("latency_type", "enqueue_to_response") != "enqueue_to_response":
        return
    method = labels.get("request_method")
    if method not in MCP_METHODS:
        return
    status = labels.get("tunnel_service_status", "")

    if sample.name == COMMAND_COUNT:
        count = _count_value(sample.value)
        if status == SUCCESS_TUNNEL_STATUS:
            method_counts[method] = method_counts.get(method, 0) + count
        else:
            key = status or "unknown"
            tunnel_error_statuses[key] = tunnel_error_statuses.get(key, 0) + count
        return

    if sample.name == COMMAND_SUM and status == SUCCESS_TUNNEL_STATUS:
        method_sums[method] = method_sums.get(method, 0.0) + sample.value


def _collect_upstream_sample(
    sample: PrometheusSample,
    *,
    upstream_counts: dict[str, int],
    upstream_sums: dict[str, float],
) -> None:
    labels = sample.labels
    if labels.get("http_request_method", "POST") != "POST":
        return
    if labels.get("http_route") != "/api/mcp":
        return
    status = labels.get("http_response_status_code")
    if not status:
        return

    if sample.name == HTTP_COUNT:
        upstream_counts[status] = upstream_counts.get(status, 0) + _count_value(
            sample.value
        )
    elif sample.name == HTTP_SUM:
        upstream_sums[status] = upstream_sums.get(status, 0.0) + sample.value


def _parse_prometheus_line(line: str) -> PrometheusSample | None:
    if "{" in line:
        brace = line.find("{")
        name = line[:brace]
        end = _find_label_end(line, brace)
        if end is None:
            return None
        labels = _parse_labels(line[brace + 1 : end])
        rest = line[end + 1 :].strip()
    else:
        parts = line.split(None, 1)
        if len(parts) != 2:
            return None
        name = parts[0]
        labels = {}
        rest = parts[1]

    if name.endswith("_bucket"):
        return None
    if not name or not rest:
        return None
    value_token = rest.split(None, 1)[0]
    try:
        value = float(value_token)
    except ValueError:
        return None
    if not math.isfinite(value):
        return None
    return PrometheusSample(name=name, labels=labels, value=value)


def _find_label_end(line: str, brace: int) -> int | None:
    escaped = False
    quoted = False
    for index in range(brace + 1, len(line)):
        char = line[index]
        if escaped:
            escaped = False
            continue
        if char == "\\" and quoted:
            escaped = True
            continue
        if char == '"':
            quoted = not quoted
            continue
        if char == "}" and not quoted:
            return index
    return None


def _parse_labels(text: str) -> dict[str, str]:
    labels: dict[str, str] = {}
    index = 0
    length = len(text)
    while index < length:
        while index < length and text[index] in " ,":
            index += 1
        key_start = index
        while index < length and text[index] not in "=":
            index += 1
        if index >= length:
            break
        key = text[key_start:index].strip()
        index += 1
        if index >= length or text[index] != '"':
            break
        index += 1
        value_chars: list[str] = []
        escaped = False
        while index < length:
            char = text[index]
            index += 1
            if escaped:
                value_chars.append(_unescape_label_char(char))
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if char == '"':
                break
            value_chars.append(char)
        if key:
            labels[key] = "".join(value_chars)
        while index < length and text[index] not in ",":
            index += 1
        if index < length and text[index] == ",":
            index += 1
    return labels


def _unescape_label_char(char: str) -> str:
    if char == "n":
        return "\n"
    if char == "\\":
        return "\\"
    if char == '"':
        return '"'
    return char


def _summarize_channels(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    channels = []
    for item in value:
        if not isinstance(item, dict):
            continue
        channels.append(
            {
                "name": _string_or_none(item.get("name")),
                "enabled": _bool_or_none(item.get("enabled")),
                "server_kind": _string_or_none(item.get("server_kind")),
                "transport_kind": _string_or_none(item.get("transport_kind")),
                "probe_status": _string_or_none(item.get("probe_status")),
            }
        )
    return channels


def _find_named_route(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, list):
        return {}
    for item in value:
        if isinstance(item, dict) and item.get("name") == name:
            return item
    return {}


def _ordered_known_methods(values: dict[str, Any]) -> dict[str, Any]:
    ordered = {method: values[method] for method in MCP_METHODS if method in values}
    ordered.update(
        {method: values[method] for method in sorted(values) if method not in ordered}
    )
    return ordered


def _count_value(value: float) -> int:
    return max(0, int(value))


def _rounded(value: float, digits: int = 3) -> float:
    return round(value, digits)


def _string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _bool_or_none(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None
