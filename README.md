# OpenAI Tunnel for HA-MCP

A HACS custom integration that exposes an existing Home Assistant MCP server through
`openai/tunnel-client`.

The integration downloads the host-specific `tunnel-client` release binary on
first run, verifies the SHA256 digest, extracts each version under
`.hass_codex_tunnel_mcp/bin/<version>/`, and supervises it as a Home Assistant
subprocess. The bundled pinned fallback and first install target is `v0.0.19`.

## Install with HACS

1. In HACS, add this repository as a custom repository with category
   `Integration`.
2. Install **OpenAI Tunnel for HA-MCP**.
3. Restart Home Assistant.
4. Add the integration from **Settings > Devices & services**.

## OpenAI Setup

1. Enable Home Assistant's **MCP Server** integration.
2. Use the local/direct MCP URL, normally
   `http://homeassistant.local:8123/api/mcp` or
   `http://127.0.0.1:8123/api/mcp` when this integration runs on the HA host.
3. Create or inspect a tunnel in
   [OpenAI Platform Tunnels](https://platform.openai.com/settings/organization/tunnels).
4. Create a runtime API key in
   [OpenAI Platform API keys](https://platform.openai.com/settings/organization/api-keys)
   with **Tunnels Read** and **Tunnels Use**.
5. Configure this integration with:
   - The OpenAI tunnel ID, formatted as `tunnel_` followed by 32 lowercase hex
     characters. This value comes from the Platform Tunnels page and identifies
     the OpenAI-hosted tunnel endpoint that both this Home Assistant integration
     and ChatGPT attach to. It is not the runtime API key, Home Assistant token,
     or MCP URL.
   - The runtime API key. This is stored in the Home Assistant config entry,
     masked in the setup UI, and passed to `tunnel-client` as
     `env:CONTROL_PLANE_API_KEY`.
   - The HA-MCP server URL, such as `http://127.0.0.1:8123/api/mcp`.
   - Optionally, a Home Assistant long-lived access token. When set, the
     integration passes it to `tunnel-client` as
     `Authorization: Bearer <token>` via `env:HA_MCP_AUTH_HEADER`, not via
     process arguments. In ChatGPT, configure the app as **No Auth**.
   - If you leave the Home Assistant token blank, ChatGPT must authenticate to
     Home Assistant through OAuth. That requires Home Assistant's browser-facing
     auth URLs to be reachable by the browser during login.
   - Optional control-plane base URL/URL path for non-default environments.
   - Whether to automatically update `tunnel-client`.
6. After the tunnel status entity reports ready, add the ChatGPT connector at
   <https://chatgpt.com/plugins#settings/Connectors?create-connector=true&redirectAfter=%2Fplugins>
   and use the same OpenAI tunnel ID.

## tunnel-client Updates

Automatic `tunnel-client` updates are enabled by default and can be disabled in
the integration options. After Home Assistant starts, the updater uses its saved
last check time to resume checking GitHub once per day. Missing or overdue
checks run after a 60-second startup delay; recent checks keep their existing
24-hour deadline, with at least 60 seconds after startup. Reloading the
integration follows the same schedule, so restarts do not reset a full day's
wait. Failed checks are retried the next day.

The updater installs the newest eligible clean stable release (`vX.Y.Z`), which
can skip intermediate versions. Drafts, prereleases, and suffix build tags such
as `v0.0.8--example` are skipped. New releases are held
for at least 8 days before automatic or default manual installation, so a
just-published release is reported as deferred.

Each selected asset must match a SHA256 digest from the GitHub release metadata
or `SHA256SUMS.txt` before it is installed. The updater starts the candidate
binary first and only marks it active after the tunnel becomes ready. If the
candidate does not become ready, the integration returns to the previous
known-good version, records the failed version so automatic checks skip it, and
raises a Home Assistant repair issue. Manual updates can still retry a failed
eligible version. The explicit release override described below can also retry
a selected newer stable version before its age hold ends.

The `Tunnel-client version` sensor exposes updater attributes for the active
version, previous known-good version, latest seen version, latest eligible
version, deferred-until time, last check, last successful update, last error,
and failed update versions. It also reports `auto_update_enabled` and the UTC
`next_update_check` deadline. The deadline is omitted while Home Assistant is
still starting, while a check is running, or when automatic updates are disabled.
These settings describe tunnel-client updates; HACS updates the integration's
Python code separately.

v0.0.19 was published on October 30, 2026 at 20:26:10 UTC and becomes eligible
on November 7, 2026 at 20:26:10 UTC (21:26:10 Vienna time). Installation occurs
at the next scheduled check, not necessarily at that instant. Changing the
pinned fallback does not migrate an existing cached active version. New installs
and recovery to the pinned fallback use the pin immediately; the eight-day hold
applies to automatic updates and the default manual update action.

### Manual controls

On the integration's device page:

1. Press **Check releases** to refresh the available newer stable versions.
2. Leave **Release override (ignores age hold)** at **Latest eligible (8-day hold)**
   for normal updates, or deliberately choose a specific version such as
   `v0.0.20` to bypass the age hold for that installation.
3. Press **Install selected release**.

Choosing a version alone does not install it or change automatic updates. The
selection resets after an installation attempt and after integration reload;
it is not saved as a permanent pin. The selected release is fetched again before
installation, must still be a published newer stable version, and must pass the
same checksum and startup-readiness checks. Failed startup returns to the
previous version. The existing `update_tunnel_client` service keeps the age hold;
`rollback_tunnel_client` remains available for returning to the previous binary.

v0.0.20 becomes eligible for automatic updates on **November 14, 2026 at
22:05:17 UTC (23:05:17 CET)**, at the next scheduled check. The fallback pin stays
at v0.0.19. The [v0.0.20 assessment](docs/tunnel-client-v0.0.20.md) covers the
actual release binaries, OAuth discovery/forwarding, and the manual controls.

## Runtime Behavior

The integration validates the configured HA-MCP URL, then starts:

```bash
tunnel-client run \
  --control-plane.tunnel-id <id> \
  --control-plane.api-key env:CONTROL_PLANE_API_KEY \
  --mcp.server-url channel=main,url=<ha-mcp-url> \
  [--mcp.extra-headers "Authorization: env:HA_MCP_AUTH_HEADER"] \
  [--mcp.discovery-extra-headers "Authorization: env:HA_MCP_AUTH_HEADER"] \
  --health.listen-addr 127.0.0.1:0 \
  --health.url-file <integration-run-dir>/health.url
```

Only Linux `amd64` and `aarch64` Home Assistant OS/Supervised hosts are
supported initially. Other platforms create a Home Assistant repair issue.

## Entities

- Tunnel process state
- Tunnel ready state
- Installed `tunnel-client` version
- Tunnel MCP requests
- Tunnel MCP average latency
- Tunnel MCP errors
- Tunnel upstream requests
- Tunnel control plane (polling state, failures and retry timing)
- Tunnel MCP health (transport and startup/discovery observations)
- Tunnel active requests (with completions, failures and timeouts)
- Tunnel queue depth (with capacity and backpressure)
- Tunnel delivery retries
- Tunnel delivery failures

The ready state and update activation check poll `/readyz`; publishing a health
URL alone does not mean the client is ready. The new diagnostic sensors read
the passive `/health?details=true` snapshot once per minute. Missing components,
unsupported schemas, and older clients without this endpoint make those sensors
unavailable without affecting the existing metrics sensors.

Readiness is an MCP/OAuth startup decision, not proof that the control plane
accepted the API key: both v0.0.18 and v0.0.19 can return `/readyz` 200 while
polling receives HTTP 401. Check the control-plane health sensor as well as a
real MCP request when validating a deployment. The update activation gate uses
this upstream readiness contract and cannot detect every authentication failure.

Health observations are historical evidence, not continuous reachability checks.
In particular, HTTP MCP reports startup-probe status but cannot provide the
same-child discovery evidence available for stdio: `not_observed` does not mean
the HTTP server is broken. Tool counts are retained counts only when discovery
was observed; completeness/partial/limited flags must accompany them. Tool names,
schemas, server identities, payloads, and trace files are not collected.

These additions use the component health API introduced in v0.0.15 and expanded
in v0.0.17. See the upstream [health contract](https://github.com/openai/tunnel-client/blob/v0.0.19/docs/health.md).
v0.0.11–v0.0.18 also improve deadline/retry handling, OAuth startup discovery,
sessionless MCP compatibility, and response timing/tracing. Timing metadata and
opt-in JSONL traces are not aggregate metrics; tracing remains disabled.

v0.0.19 forwards an MCP server's response `X-Request-Id` as
`X-Tunnel-Mcp-Request-Id`; the field is optional and omitted when absent or
listed in `Connection`. Its stdio child recovery does not apply to this
integration's HTTP MCP transport. No new stdio flags or YAML keys are added,
so the command remains compatible with v0.0.18 rollback. Bounded DNS diagnostics
add log context without changing DNS resolution. See the
[v0.0.19 compatibility assessment](docs/tunnel-client-v0.0.19.md) for test scope
and the remaining Home Assistant checks.

The metrics entities summarize the local `tunnel-client` `/metrics` and
`/api/status` endpoints. They intentionally omit Go runtime/process/promhttp
internals, raw histogram buckets, raw logs, mutable admin endpoints, API keys,
and Home Assistant bearer tokens. The integration reports whether the local
`tunnel-client` UI is available, but does not iframe or proxy it into Home
Assistant because that UI exposes broader local admin functionality.

## Services

- `hass_codex_tunnel_mcp.restart_tunnel`
- `hass_codex_tunnel_mcp.redownload_tunnel_client`
- `hass_codex_tunnel_mcp.check_tunnel_client_update`
- `hass_codex_tunnel_mcp.update_tunnel_client`
- `hass_codex_tunnel_mcp.rollback_tunnel_client`

`check_tunnel_client_update` refreshes GitHub release status without installing
anything. `update_tunnel_client` installs the latest eligible verified release
and uses the same health-gated activation and rollback path as automatic
updates. `rollback_tunnel_client` switches back to the previous known-good
version when one is available.

## Development Checks

Use Python 3.14.2 or newer for the Home Assistant 2026.10.1 regression tests:

```bash
python -m pip install -r requirements-test.txt
python -m compileall custom_components tests
python -m pytest
```

The scheduler tests run Home Assistant's real timers and job dispatcher
with a controlled clock. They cover startup and reload scheduling, daily checks,
event-loop safety, serialized installation with independent config entries,
unload callbacks, transient
failures, and the existing eight-day eligibility, checksum rejection, and
readiness rollback behavior.

Optional release tests launch the checksum-verified v0.0.18, v0.0.19 and v0.0.20 binaries
against loopback MCP/control-plane fixtures through the real integration process
manager and updater. They use fake credentials and never connect to a live HA
installation. See the [reproduction commands](docs/tunnel-client-v0.0.20.md#reproduce).
GitHub Actions runs the complete suite and these tests on Linux amd64 and arm64.
