# tunnel-client v0.0.20 compatibility and manual update controls

Assessed November 7, 2026 against integration main
[`f8513fa`](https://github.com/norpol/hass-codex-tunnel-mcp/commit/f8513fa49db647b43b97a574b7d18138c7ecd483),
which includes merged PR #7, and upstream
[`c2144b0`](https://github.com/openai/tunnel-client/tree/c2144b0904980bcbae2636cdca4f4167e9818e0b).
This extends the existing v0.0.19 regression harness instead of creating a
second HTTP/process/updater harness. No live Home Assistant was accessed.

## Recommendation

Leave the new OAuth forwarding configuration unset for the integration's
existing HTTP MCP/static-token and forwarded-bearer configurations. No transport,
command-line, credential, health-parser, or process-manager change is required
by v0.0.20 in the exercised paths. No new deployment regression was found in
those paths. Keep the v0.0.19 fallback pin, the eight-day automatic-update hold,
daily scheduler, checksum verification, readiness activation, and rollback.

The separate requested feature adds two device buttons and a release selector:
**Check releases**, **Release override (ignores age hold)**, and
**Install selected release**. Its default respects the hold. Selecting a specific
newer stable release explicitly permits an early manual installation. The choice
is consumed on an installation attempt and is never consulted by automation or
persisted across reload. Automatic calls reject an explicit version override.
This is an operator-requested exception for one manual attempt, not a reduction
of the automatic safety policy. The existing update service remains age-gated.

## Release evidence and timing

[Release metadata](https://api.github.com/repos/openai/tunnel-client/releases/tags/v0.0.20)
reports `published_at=2026-11-06T22:05:17Z`. Both official Linux archives were
downloaded independently and hashed; they match the published asset digests:

| Architecture | ZIP SHA-256 |
| --- | --- |
| amd64 | `2f06ce5a17425c74a827e7038692936e973ae388e7eec728c40efee1fb5f139d` |
| arm64 | `2d89946c23111eb910f1bd4e7c5c4bfedfd73ac0dc9828aefc36294cfeae491f` |

Automatic eligibility is **2026-11-14 22:05:17 UTC / 23:05:17 CET**, followed
by installation at the next daily check. Tests exercise one microsecond before,
exactly at, and after this boundary. No future check or live update was scheduled.

## OAuth forwarding and discovery

[Configuration](https://github.com/openai/tunnel-client/blob/v0.0.20/docs/configuration.md#oauth-forwarding-origins)
and [registration code](https://github.com/openai/tunnel-client/blob/v0.0.20/pkg/runtimeharpoon/host_registration.go)
separate two decisions:

- `mcp.oauth_trusted_origins` permits fetching metadata from separate origins.
- `harpoon.oauth_forward_origins` permits registering discovered OAuth endpoints
  there for forwarded requests. It does not grant discovery trust, introduce
  arbitrary targets, widen credential scope, or authorize redirects.

The forwarding list is empty by default and the old registration policy still
runs. Entries are exact HTTPS origins, including effective port; unrelated
ports and subdomains are not covered. For a future deployment requiring a
separate identity provider, both lists may be necessary, including the metadata
source and each required endpoint origin. That is a distinct topology/configuration
feature, not a required change to current HTTP MCP bearer forwarding.

The integration inherits its parent environment. "Unset" therefore assumes
`HARPOON_OAUTH_FORWARD_ORIGINS` has not been injected externally. Tests clear
inherited configuration and use explicit synthetic values for the opt-in cases.
Do not derive this list automatically from discovered metadata or the HTTP MCP URL.

Older releases reject the new CLI flag/YAML key; older binaries do not gain the
feature from the environment variable. Leaving these settings absent preserves
rollback compatibility. All active pollers would need equivalent configuration
and a supporting binary before relying on the new forwarding path.

The [retry classification change](https://github.com/openai/tunnel-client/blob/v0.0.20/pkg/oauth/discovery_retry_policy.go)
keeps timeouts retryable despite another candidate's 404. Permanent trust or
other non-timeout failures retain precedence. The real-binary control experiment
reproduced v0.0.19 settling into failed discovery/non-ready state; v0.0.20 stayed
non-ready while retrying and became ready after metadata recovered, for both
response-header and response-body timeouts. The integration's 60-second update
activation timeout is unchanged: longer outages can still cause safe rollback.

## Executed tests and their boundaries

All binary tests use official, hash-checked ZIPs, synthetic credentials, temporary
storage, loopback HTTP MCP/control-plane fixtures, and a temporary trusted TLS
certificate for the separate OAuth origin. No production key, HA endpoint, or
hosted OpenAI tunnel is used. The real integration launches/stops the binaries.

| Area | Executed assertions |
| --- | --- |
| Existing HTTP coverage | v0.0.18/v0.0.19/v0.0.20 initialization, sessions, tool discovery/calls, static and forwarded bearer auth, rejection of missing/wrong tokens, optional request-ID propagation, passive health, HTTP 503 recovery |
| Processes | Unexpected whole-client exit is reported; explicit restart restores service; orderly stop reaps the process |
| Updates | v0.0.18 → v0.0.19, v0.0.18 → v0.0.20, and v0.0.19 → v0.0.20, readiness activation, failed-candidate rollback and automatic skip, explicit rollback without old saved asset metadata |
| Installation | Updater installs into a separate uncached directory; only release metadata/network download and HA repair writes are mocked, with download returning the actual official ZIP bytes |
| Manual early install | Actual v0.0.19 → v0.0.20 before eligibility, successful activation/explicit rollback and failed-readiness rollback using the same updater path |
| Discovery retry | Actual five-second deadlines with another candidate returning 404; both stalled response headers and stalled response bodies; readiness remains pending until recovery |
| Default forwarding | Discovery trust alone produces no separate-origin forwarding channel in either v0.0.19 or v0.0.20; authenticated HTTP MCP calls still work |
| Opt-in forwarding | v0.0.20 discovers/registers TLS OAuth endpoints, forwards metadata and a synthetic code exchange, preserves the resource audience, and forwards the returned bearer token for an MCP tool call |
| Security boundaries | Separate unapproved HTTPS port absent from target catalog; direct target call and redirect blocked; no request reaches that server; MCP discovery secret absent on OAuth requests; forwarding approval alone does not grant discovery trust |
| Probe diagnostics | Expected auth-required startup probe emits an upstream INFO record; no prior connection-error or compatibility-warning text |
| Manual UI/policy | Per-entry check/select/install, default hold, one-shot reset after failure, published-newer-stable validation, checksum/readiness failures, automatic override rejection; actual HA EntityComponent state/listener lifecycle |

Local environment: Linux amd64, Python 3.14.9, Home Assistant 2026.10.1. The
unchanged PR #7 baseline produced 95 passes and one failure in stale-process
cleanup: this sandbox exposes different subprocess and `/proc` PID namespaces.
The final local run passed **130 tests**, with only that unchanged check
deselected. Compileall, focused Ruff undefined/unused-name checks, and
`git diff --check` passed. The stale-process check remains enabled in the
native Linux amd64/arm64 CI matrix. Consult this PR's checks for final counts
and native architecture execution results.

## Remaining limits

- This is fixture-based compatibility evidence, not live HA/hosted-service
  deployment sign-off. Actual HA tools, browser login/consent/refresh, network
  failures against the hosted service, and sustained operation are unverified.
  The synthetic code exchange is not a complete OAuth authorization flow.
- Actual HA entity lifecycle is tested; browser rendering of the device page and
  complete custom-platform reload/unload in a disposable HA deployment are not.
- As in v0.0.18/v0.0.19, `/readyz` may return 200 while the control-plane API key
  is rejected with 401. This is tested in v0.0.20; component health reports it.
  Readiness alone is not end-to-end authentication evidence.
- Whole-process recovery here means the existing explicit restart action.
  There is no new automatic crash restart. Stdio child recovery does not apply
  to this integration's HTTP MCP transport.
- Origin normalization/configuration precedence beyond the tested cases,
  multiple pollers, and runtime-flavor binaries were source-reviewed or remain
  outside this integration's executable tests. Native arm64 status must come
  from CI, not the local amd64 run or an archive hash.

## Reproduce

On Linux amd64 or arm64 with Python >=3.14.2:

```bash
python -m pip install -r requirements-test.txt
arch=amd64 # use arm64 on aarch64
archive_dir="$(mktemp -d)"
for version in v0.0.18 v0.0.19 v0.0.20; do
  asset="tunnel-client-${version}-linux-${arch}.zip"
  curl --fail --location --retry 3 --max-time 120 \
    "https://github.com/openai/tunnel-client/releases/download/${version}/${asset}" \
    --output "$archive_dir/$asset"
done
TUNNEL_CLIENT_ARCHIVE_DIR="$archive_dir" python -m pytest -q
python -m compileall -q custom_components tests
git diff --check
```

Without the archive environment variable, binary tests explicitly skip. Once
opted in, a missing/corrupt archive fails. Use the full suite on ordinary Linux;
only the known sandbox needs `-k 'not test_tunnel_manager_cleans_up_stale_client'`.
