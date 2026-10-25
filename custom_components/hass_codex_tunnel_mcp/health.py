"""Curated, passive tunnel-client health observations (schema version 1)."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class TunnelHealthSummary:
    """Only explicitly selected health fields, suitable for HA attributes."""

    live: bool | None = None
    ready: bool | None = None
    truncated: bool = False
    components: dict[str, dict[str, Any]] = field(default_factory=dict)


def _identifier(value: Any) -> str | None:
    if isinstance(value, str) and re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", value):
        return value
    return None


def _timestamp(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return value if parsed.tzinfo is not None else None


def _fields(
    payload: Any,
    *,
    identifiers: str = "",
    integers: str = "",
    numbers: str = "",
    booleans: str = "",
    timestamps: str = "",
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    result = {}
    for key in identifiers.split():
        if (value := _identifier(payload.get(key))) is not None:
            result[key] = value
    for key in timestamps.split():
        if (value := _timestamp(payload.get(key))) is not None:
            result[key] = value
    for key in integers.split():
        value = payload.get(key)
        if type(value) is int and value >= 0:
            result[key] = value
    for key in numbers.split():
        value = payload.get(key)
        if type(value) in (int, float) and 0 <= value < math.inf:
            result[key] = value
    for key in booleans.split():
        if type(value := payload.get(key)) is bool:
            result[key] = value
    return result


DETAIL_FIELDS = {
    "control-plane": {
        "integers": "consecutive_failures http_status",
        "numbers": "current_poll_age_seconds effective_wait_seconds deadline_seconds",
        "identifiers": "failure_category",
        "timestamps": "last_attempt last_success last_error next_retry",
    },
    "response-delivery": {
        "integers": "in_progress http_status attempts retries accepted completed terminal_failures",
        "identifiers": "disposition failure_category",
        "timestamps": "last_accepted last_completed last_failure",
    },
    "queue": {
        "integers": "depth capacity enqueued dequeued",
        "numbers": "utilization backpressure_seconds",
        "timestamps": "last_enqueue last_dequeue",
    },
    "dispatcher": {
        "integers": "active pool_limit completed failures timeouts",
        "numbers": "oldest_active_age_seconds",
        "booleans": "accepting",
        "timestamps": "last_start last_completion",
    },
    "mcp": {
        "identifiers": "transport evidence child_state",
        "integers": "initialize_epoch server_discover_epoch",
    },
    "oauth": {"booleans": "discovery_complete"},
    "harpoon": {"integers": "target_count", "identifiers": "catalog_state"},
    "cloudflared": {"booleans": "enabled ready"},
}


def summarize_health_payload(payload: Any) -> TunnelHealthSummary:
    """Reject unknown schemas; omit unknown fields and sensitive metadata.

    Tool names, server identity, raw errors, and runtime/child identifiers are
    deliberately excluded. HTTP MCP has only startup-probe evidence: an
    unobserved tools list must never be presented as an empty catalog.
    """
    if (
        not isinstance(payload, dict)
        or type(payload.get("schema_version")) is not int
        or payload["schema_version"] != 1
    ):
        raise ValueError("Unsupported tunnel-client health schema")
    root = _fields(payload, booleans="live ready truncated")
    components = payload.get("components")
    summaries = {}
    if isinstance(components, dict):
        for name, fields in DETAIL_FIELDS.items():
            component = components.get(name)
            if not isinstance(component, dict):
                continue
            summary = _fields(
                component,
                identifiers="status state reason_code",
                booleans="limited",
                timestamps="observed_at",
            )
            details = component.get("details")
            summary["details"] = _fields(details, **fields)
            if name == "mcp" and isinstance(details, dict):
                summary["details"].update(_mcp_observations(details))
            summaries[name] = summary
    return TunnelHealthSummary(components=summaries, **root)


def _mcp_observations(details: dict[str, Any]) -> dict[str, Any]:
    result = {}
    for name in ("initialize", "server_discover", "tools_list", "startup_probe"):
        observation = details.get(name)
        if not isinstance(observation, dict):
            continue
        summary = _fields(
            observation,
            identifiers="state",
            booleans="ok limited complete partial identity_complete",
            timestamps="observed_at",
        )
        if observation.get("ok") is True:
            if name == "tools_list":
                summary.update(_fields(observation, integers="retained_count"))
            if name == "initialize":
                summary.update(_fields(observation, identifiers="protocol_version"))
            for key in ("capability_names", "supported_versions"):
                values = observation.get(key)
                if isinstance(values, list):
                    summary[key] = sorted(
                        {
                            value
                            for value in values[:32]
                            if _identifier(value) is not None
                        }
                    )
        result[name] = summary
    return result
