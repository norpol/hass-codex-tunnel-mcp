# tunnel-client v0.0.19 compatibility

Assessment date: November 1, 2026. Integration baseline:
[`7d3485c`](https://github.com/norpol/hass-codex-tunnel-mcp/commit/7d3485c044644643a4ff937a1bebf3869262ad8d),
including merged PRs #3–#6. Upstream reviewed from v0.0.18 to
[`v0.0.19` / `01d67bb`](https://github.com/openai/tunnel-client/tree/01d67bb4da0d8359b808b4761f8db171ab682cd7).

No HTTP transport, authentication, process-manager, health parser, or updater
implementation change is required by this release. The contribution updates the
fallback metadata, adds regression tests, and preserves the existing updater.
This is automated compatibility evidence, not a live HA deployment sign-off.

## Release and download verification

[GitHub release metadata](https://api.github.com/repos/openai/tunnel-client/releases/tags/v0.0.19)
reports publication at `2026-10-30T20:26:10Z`. Both Linux ZIPs were downloaded
and hashed independently; they match the API's asset digests:

| Architecture | ZIP SHA-256 |
| --- | --- |
| amd64 | `9ee9448592345a9dd1517fb79791ab54d24f051142eb92bcbe8cd355effc9142` |
| arm64 | `5a19e556c4a8736ab7a6fde6f0568f6e7411fa2dff21186fc15bc30e9b98a603` |

Automatic eligibility remains publication plus eight days:
`2026-11-07T20:26:10Z` (21:26:10 CET). Tests exercise one microsecond before,
exactly at, and after this boundary. Normal daily scheduling remains unchanged.
The fallback is also the first-install target and is not age-gated; installing
this integration revision on a fresh instance can therefore use v0.0.19 sooner.
An existing saved v0.0.18 with an executable cache remains active until updated.

## Automated coverage

The release tests install the official archives through `install_from_zip_bytes`
and run the real binaries via `TunnelManager`. Both fake servers bind to
`127.0.0.1`; credentials and tools are synthetic. Release-list fetches and repair
issue writes in the update test are mocked, while installation, processes,
readiness polling, version selection, and rollback run the integration code.

| Area | Assertions |
| --- | --- |
| HTTP MCP | Initialize, initialized notification, session header, tools/list, tools/call, HTTP 503 and recovery on the same client |
| Authentication | Environment-backed static HA token reaches startup/runtime MCP requests; forwarded bearer tokens work; missing/invalid forwarded tokens produce 401; HA token does not reach the control plane |
| Request IDs | Connector request ID reaches MCP; v0.0.19 maps response `X-Request-Id` to `X-Tunnel-Mcp-Request-Id`; absent and Connection-nominated IDs are omitted; v0.0.18 tolerates their absence |
| Health | Actual `/readyz`, `/health?details=true`, `/metrics`, `/api/status`; schema-1 summarization; health reads do not issue MCP calls; control-plane 401 is visible without exposing credentials |
| Supervision | Unexpected whole-client exit becomes unhealthy/exited; explicit restart restores service; orderly stop reaps the process; existing tests cover cancellation-safe stop and stale process cleanup |
| Updates | Pre-boundary check keeps v0.0.18; eligible verified v0.0.19 becomes active only after readiness; prior process exits before replacement; failed MCP startup rolls back and records/skips the candidate |
| Rollback | Real v0.0.19 → v0.0.18 switch using the same command and an old cached binary without saved asset metadata; requests succeed after rollback |
| Download protection | Exact filename/version/hash/URL checks for both pins; bad checksum cannot overwrite an installed executable; existing digest/SHA256SUMS fallback and scheduler regressions |

The initial local baseline passed 83 tests and failed the stale-process test
because the execution sandbox's `/proc` PIDs do not match subprocess PIDs
(`os.getpid()` differs from `/proc/self`). This is an environment limitation,
not a v0.0.19 change; production cleanup and that regression test are unchanged.
With Python 3.14.8 and Home Assistant 2026.10.1, the updated local suite passed
95 tests with only that known environment-dependent test deselected. Compileall
and `git diff --check` also passed.
The GitHub Actions matrix runs the full suite, including stale-process cleanup
and the real binaries, on ordinary Linux amd64 and arm64 runners. Consult the
PR checks for the recorded results; local execution is amd64 only.

## Compatibility findings and limits

- **Readiness is not an authentication guarantee.** In both versions, a wrong
  runtime API key can leave `/readyz` at 200 while control-plane health reports
  `http_status=401`, `failure_category=http_error`, and consecutive failures.
  The current update gate inherits this limitation. The static-token and OAuth
  startup paths also cannot replace a real authenticated tool call.
- **Stdio recovery is out of scope for this integration.** It starts HTTP MCP
  with `--mcp.server-url`, not an MCP child process. No
  `--mcp.stdio-failure-policy` flag or YAML field is introduced; older binaries
  reject those new settings. Whole-client crashes are reported by the manager;
  its existing explicit restart path is tested, not automatic crash restart.
- **DNS diagnostics were source-reviewed.** The new warning is once per
  transport, uses bounded classifications, and does not change resolver choice,
  retry policy, or health categorization. Runtime DNS failure injection and
  Android/Termux behavior were not tested.
- **No live HA or hosted OpenAI tunnel was used.** Browser OAuth authorization,
  token refresh, actual HA tools, HA OS/container reload/unload, and a sustained
  hosted-tunnel session still require integration testing. The tests exercise
  bearer forwarding, not a complete OAuth login. A real HA MCP server need not
  emit `X-Request-Id`; absence is valid.

Before deployment sign-off, use a disposable HA instance: configure its MCP
integration, test a harmless tool through a hosted tunnel with both supported
auth modes, inspect ready/control-plane/MCP sensors, restart/reload/unload, and
exercise upgrade and rollback with the retained old binary. Do not change the
eight-day policy to accelerate this validation. No live installation was changed
during this assessment.

## Reproduce

On a Linux amd64 or arm64 machine with Python >=3.14.2:

```bash
python -m pip install -r requirements-test.txt
python -m compileall -q custom_components tests
python -m pytest -q

# Choose amd64 for x86_64 or arm64 for aarch64.
arch=amd64
archive_dir="$(mktemp -d)"
for version in v0.0.18 v0.0.19; do
  asset="tunnel-client-${version}-linux-${arch}.zip"
  curl --fail --location --retry 3 --max-time 120 \
    "https://github.com/openai/tunnel-client/releases/download/${version}/${asset}" \
    --output "$archive_dir/$asset"
done
TUNNEL_CLIENT_ARCHIVE_DIR="$archive_dir" python -m pytest -q
```

The opt-in release tests verify each archive against fixed hashes before
extraction/execution, make no downloads themselves, and use temporary directories.
Without the environment variable, those tests are explicitly skipped. A missing
or corrupt archive after opt-in fails the tests. No real credentials are needed.
