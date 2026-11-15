# Proposal

## Why

Executable updates already have eligibility, verification, recovery, and
scheduling behavior worth preserving. Capture a small contract backed by the
existing tests, including a direct check of failed rollback reporting.

## What Changes

- Document existing update policy, downloaded-archive verification, readiness
  activation and recovery, and automatic scheduling in one capability.
- Map scenarios to tests in tasks and add focused checks for uncovered failure
  paths. Update the README capability inventory and project context.
- Preserve current runtime behavior, including the limits of readiness checks
  and the possibility that rollback itself fails.

## Capabilities

### New Capabilities

- `tunnel-client-updates`: Existing executable update eligibility, checksum
  verification, activation/recovery, and automatic scheduling guarantees.

### Modified Capabilities

None. The installation spec continues to describe the maintenance guide; this
capability describes updater behavior.

## Impact

Specifications, contributor documentation, and focused regression tests change.
No runtime code, credentials, release eligibility, fallback pin, or rollback
policy changes. A design document is unnecessary: there is no new implementation
or architectural decision; evidence and limitations are recorded with tasks.
