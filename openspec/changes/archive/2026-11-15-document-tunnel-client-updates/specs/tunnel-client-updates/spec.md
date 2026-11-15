# Spec Delta

## Purpose

Preserve the integration's existing executable update eligibility, verification,
activation and recovery, and automatic scheduling behavior.

## ADDED Requirements

### Requirement: Select eligible releases

Automatic and default manual updates SHALL select the newest stable `vX.Y.Z`
release newer than the active version and published at least eight days earlier.
Drafts, prereleases, and suffix tags SHALL be excluded. Automatic updates SHALL
skip recorded failed versions; manual installation SHALL allow retrying an
otherwise eligible failed version. Only an explicit manual selection SHALL
bypass the age hold for one installation attempt.

#### Scenario: A release reaches the age boundary

- **WHEN** a newer stable release is younger than eight days
- **THEN** automatic and default manual updates leave it deferred
- **AND** it becomes eligible at exactly eight days, subject to the same release selection policy

#### Scenario: An operator selects an early release

- **WHEN** an operator selects a specific newer stable release
- **THEN** selection alone neither installs it nor changes automatic or default manual updates
- **AND** installing that selection rechecks its release metadata and bypasses only the age hold
- **AND** the UI selection resets after the attempt, including failure, and on integration reload

#### Scenario: A selected release is no longer valid

- **WHEN** the selected release is absent, unpublished, a draft, a prerelease, a suffix tag, or not newer than the active version
- **THEN** installation is rejected before the candidate is launched

### Requirement: Verify downloaded candidates

Downloaded candidate archives SHALL match the SHA-256 digest from GitHub release
metadata or its `SHA256SUMS.txt` before extraction and launch. Missing digests
and checksum mismatches SHALL prevent candidate launch and raise an unverified
release repair. Automatic, default manual, and explicit manual updates SHALL
retain this verification.

#### Scenario: Verification cannot establish archive integrity

- **WHEN** a candidate has no usable digest or its downloaded archive does not match that digest
- **THEN** the candidate is not launched and the active version is unchanged
- **AND** an unverified release repair is raised, including for a manual age override

### Requirement: Activate ready candidates and recover

The updater SHALL mark a candidate active only after startup readiness succeeds,
retaining the replaced version for rollback. Candidate startup failure SHALL
record the failed version, raise an update repair, and attempt to restart the
previous active version. Failure of that recovery SHALL raise a rollback repair.
Readiness is the client's `/readyz` result, not proof of control-plane
authentication or a successful end-to-end MCP request.

#### Scenario: A candidate becomes ready

- **WHEN** a verified candidate is started
- **THEN** its version is not recorded as active until readiness succeeds
- **AND** success records the candidate as active, the replaced version as previous, and the successful update time

#### Scenario: Candidate startup fails

- **WHEN** the candidate fails to start or does not become ready
- **THEN** the previous active version is restored in updater state and a restart of it is attempted
- **AND** the candidate is recorded as failed and an update repair is raised
- **AND** later automatic checks skip that failed candidate

#### Scenario: Recovery also fails

- **WHEN** restarting the previous active version fails or it does not become ready
- **THEN** a rollback repair reports the recovery failure in addition to the update repair
- **AND** the failed candidate is not recorded as a successful update

### Requirement: Resume and stop automatic checks safely

Enabled automatic updates SHALL check daily after Home Assistant starts, using
the saved last-check time across reloads and at least a 60-second startup delay.
Failed checks SHALL retain a next-day retry schedule. Disabling automatic
updates SHALL prevent automatic fetching and installation. Successful entry
unload SHALL cancel pending and running automatic checks and await in-flight
cleanup without rescheduling or stopping another entry's updater.

#### Scenario: Reload follows a recent check

- **WHEN** the saved check is less than 24 hours old
- **THEN** the next check is scheduled for 24 hours after it, or 60 seconds after startup if later
- **AND** repeated reloads do not restart a full 24-hour wait

#### Scenario: There is no usable recent check

- **WHEN** the saved check is missing, overdue, invalid, or in the future
- **THEN** the updater waits until Home Assistant has started and a 60-second startup delay has elapsed
- **AND** it checks then and resumes daily checks

#### Scenario: A check fails and the entry reloads

- **WHEN** fetching releases fails and the integration subsequently reloads
- **THEN** the failed attempt's saved time preserves the next-day retry deadline, subject to the minimum startup delay
- **AND** future checks remain scheduled

#### Scenario: Automatic updates are disabled

- **WHEN** automatic updates are disabled for an entry
- **THEN** its automatic checks neither fetch releases nor install a candidate

#### Scenario: An entry unloads during scheduled work

- **WHEN** an entry successfully unloads with a pending or running automatic check
- **THEN** that check is cancelled and not rescheduled
- **AND** any in-flight installation or process cleanup completes before unload returns
- **AND** other entries retain their own update schedules
