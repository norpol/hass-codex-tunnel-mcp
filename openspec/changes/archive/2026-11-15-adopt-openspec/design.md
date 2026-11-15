# Design

## Context

See [proposal.md](proposal.md) for motivation and scope. The baseline is grounded
in the implementation and README merged by
[PR #14](https://github.com/norpol/hass-codex-tunnel-mcp/pull/14), including its
successful amd64 and arm64 checks. The existing Python test environment targets
Home Assistant 2026.10.1; Marketplace instructions concern the 2026.11 UI.

## Goals / Non-Goals

**Goals:** establish testable capability contracts, connect Codex to the standard
OpenSpec workflow, and validate authored specifications in CI.

**Non-Goals:** changing integration execution, updating the supported Home
Assistant minimum, or treating external Marketplace behavior as code implemented
by this repository. README owns Marketplace/HACS procedures; the installation
capability retains only integration-specific guarantees.

## Decisions

### Standard schema and local Codex skills

Use OpenSpec 2.0.0's `spec-driven` schema and generated core Codex skills. Keep
project constraints in `openspec/config.yaml`, with behavior in capability specs
and implementation/test mapping in designs or tasks. Use `skip_specs: true` in
change metadata when contracts are unchanged, and include design only when the
standard schema's complexity criteria apply. No custom schema is needed.

The adoption adds new spec entries for existing behavior. Use ADDED requirement
deltas, validate them, and archive the completed adoption to create the baseline
under `openspec/specs/`. Do not invent an earlier planning history for PR #14 or
leave its already-merged behavior marked as pending implementation. Define
retryable probe failures once and use separate startup/running scenarios under
one repair-timing requirement. Keep Marketplace/HACS navigation and migration
instructions in README rather than duplicating them in capability requirements.

### Pinned CLI independent of the Home Assistant runtime

CI uses the official v2.0.0 Linux x64 archive, checks its pinned SHA-256 before
extraction, and runs strict validation plus the archived-task check. Disable
telemetry for tool invocations. On NixOS, the official tagged Nix package provides
the same CLI version; the generic Linux executable requires a compatible loader.
Using npm would require an additional developer Node installation, while making
OpenSpec a Home Assistant dependency would put development tooling in the runtime.

Keep generated Codex skills in version control so a checkout has the workflows.
Review regenerated files when upgrading the CLI and update the version, archive
checksum, generated skills, and developer instructions together.

### Verification map

The following tests already exist. Run the startup/probe subset to confirm the
baseline; no runtime test implementation is needed for this tooling change.

| Requirement or scenario | Evidence |
| --- | --- |
| Route appears after startup; isolated failed attempt; no duplicate startup error | `tests/test_setup_retry.py::test_route_appearing_after_startup_retries_automatically` |
| Persistent 404/503, timeout, connection failure; repair only when fully running | `tests/test_setup_retry.py::test_unavailable_endpoint_retries_with_repair_only_after_startup` |
| Recovery clears earlier repairs and establishes one successful setup | `tests/test_setup_retry.py::test_persistent_failure_recovers_and_clears_repairs` |
| Invalid persisted URL remains visible during startup | `tests/test_setup_retry.py::test_invalid_url_still_creates_repair_during_startup` |
| Unexpected tunnel exceptions remain visible | `tests/test_setup_retry.py::test_unexpected_start_failure_is_still_logged` |
| Successful responses, 401/403/405, optional bearer header | `tests/test_mcp_url.py::test_probe_accepts_reachable_endpoint` |
| Sanitized handled HTTP 404/server errors, timeouts, connection failures, and exception chains | `tests/test_mcp_url.py::test_probe_failure_is_retryable_and_sanitized` and startup log assertions |
| Repair URL redaction | `tests/test_mcp_url.py::test_redact_mcp_url_removes_secret_path` and startup repair assertions |
| Supported binary architectures | `tests/test_binary.py::test_select_asset_for_linux_amd64_and_arm64` and platform selection/setup code review |
| Separate executable hold, override, verification, and rollback guidance | README/code review plus existing `tests/test_manual_update_controls.py` and `tests/test_updater.py` coverage |
| Maintenance connection guidance | Manual README review and the updater's tunnel stop/start sequence |

The redaction contract covers failures converted to `MCPProbeError`: HTTP
404/server errors raised as `HTTPError`, and `TimeoutError`, `OSError`, or
`URLError` failures. It does not cover every HTTP or transport exception.
`http.client.BadStatusLine` and `http.client.InvalidURL` currently escape that
conversion and reach the generic setup error log and repair with their original
details. Extending redaction to those cases requires runtime handling and
regression tests; this baseline correction does not change runtime behavior.

Authoritative external documentation:

- [Home Assistant Marketplace](https://www.home-assistant.io/integrations/marketplace/)
- [Home Assistant 2026.11 release](https://www.home-assistant.io/blog/2026/11/04/release-202611/)
- [Home Assistant setup retries](https://contributors.home-assistant.io/core/integrations/setup-failures/)
- [OpenSpec 2.0.0 workflow](https://github.com/Fission-AI/OpenSpec/blob/v2.0.0/docs/getting-started.md)
- [OpenSpec 2.0.0 CLI reference](https://github.com/Fission-AI/OpenSpec/blob/v2.0.0/docs/cli.md)

Structural validation does not prove semantic correctness. Review each scenario
against this evidence, check links, and scan the complete staged content and
commit metadata for secrets and personal information before publishing.

## Risks / Trade-offs

- **Partial baseline:** only two capabilities are specified. Name that limit in
  project context and developer guidance so future work does not assume complete
  integration coverage.
- **External UI drift:** keep versioned Marketplace/HACS instructions and official
  source links in README, with no duplicate UI procedures in the baseline.
- **Generated skill drift:** upstream workflows can change. Pin the generator
  version and review regeneration instead of editing its generated output.
- **Downloaded tooling:** verify the CI archive before execution and retain the
  read-only GitHub token permissions used by the existing workflow.

## Migration Plan

Initialize the repository, author and validate the adoption artifacts, run the
mapped regression subset, and complete the developer guide and CI job. Once the
adoption tasks are complete, sync its specs and archive the change. Validate the
resulting baseline and completed archive again before opening a new PR.

This migration changes developer files only. Reverting the adoption removes the
tooling and specifications without altering deployed tunnel configuration,
credentials, update eligibility, or rollback state.
