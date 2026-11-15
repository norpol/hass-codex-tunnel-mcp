# Proposal

## Why

The startup retry and Marketplace work merged in PR #14 has regression tests and
user documentation, but no durable capability specifications. Establishing a
small OpenSpec baseline makes those contracts explicit for future changes.

## What Changes

- Add OpenSpec 2.0.0 configuration and its generated Codex core workflows.
- Specify existing MCP startup and integration-specific installation/maintenance
  guarantees; keep Marketplace/HACS procedures in README. No runtime behavior changes.
- Add a pinned, checksum-verified OpenSpec validation job and development guidance,
  including the standard lighter workflow for maintenance with unchanged contracts.
- Validate and archive this adoption change, retaining the baseline specs.

## Capabilities

### New Capabilities

These are new specification entries for behavior already implemented and
documented, not new integration features.

- `mcp-startup`: MCP endpoint reachability, startup retries, repair timing,
  recovery, and safe diagnostics.
- `marketplace-installation`: Supported hosts, separate integration/executable
  updates, and maintenance connection guidance.

### Modified Capabilities

None; the repository has no previous OpenSpec capability inventory.

## Impact

Development tooling, CI, specifications, and README guidance change. Home
Assistant configuration, public actions, credentials, binary update eligibility,
and rollback behavior retain their implemented contracts. A complete baseline
for authentication, updater internals, and diagnostics is outside this adoption.
