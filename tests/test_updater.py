from __future__ import annotations

from custom_components.hass_codex_tunnel_mcp.updater import TunnelClientUpdaterState


def test_updater_state_round_trips_status_fields() -> None:
    state = TunnelClientUpdaterState.from_dict(
        {
            "active_version": "v0.0.11",
            "previous_version": "v0.0.10",
            "latest_seen_version": "v0.0.12",
            "latest_eligible_version": "v0.0.11",
            "deferred_until": "2026-07-20T00:00:00+00:00",
            "last_check": "2026-07-12T00:00:00+00:00",
            "last_update": "2026-07-12T01:00:00+00:00",
            "last_error": "candidate failed",
            "failed_versions": {"v0.0.12": "not ready"},
            "active_asset": {
                "version": "v0.0.11",
                "os_name": "linux",
                "arch": "amd64",
                "filename": "tunnel-client-v0.0.11-linux-amd64.zip",
                "sha256": "a" * 64,
            },
        }
    )

    assert state.to_dict()["active_version"] == "v0.0.11"
    assert state.to_dict()["failed_versions"] == {"v0.0.12": "not ready"}
    assert state.as_attributes()["active_version"] == "v0.0.11"
    assert state.as_attributes()["failed_update_versions"] == ["v0.0.12"]
