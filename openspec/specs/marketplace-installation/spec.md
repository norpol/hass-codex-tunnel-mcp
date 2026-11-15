# marketplace-installation Specification

## Purpose

Define integration-specific installation and maintenance guarantees. The README
owns Marketplace and legacy HACS instructions, including navigation, migration,
and the explanation of Home Assistant's custom-integration warning.

## Requirements

### Requirement: Distinguish integration updates from executable updates

The maintenance guide SHALL distinguish Marketplace or legacy HACS integration-code
updates from the integration's own tunnel-client updater. It SHALL explain that
Marketplace uses hacs.json metadata and does not control the executable's
eight-day hold, manual override, or rollback policy.

#### Scenario: Updating integration code

- **WHEN** an operator wants to update this repository's integration code
- **THEN** the guide identifies Marketplace or legacy HACS as its update mechanism
- **AND** it explains that a Home Assistant restart applies the update

#### Scenario: Updating the managed executable

- **WHEN** an operator wants to update tunnel-client
- **THEN** the guide describes its automatic checks and device-page manual controls
- **AND** it distinguishes the default age hold from an explicit one-shot release override
- **AND** it explains checksum verification and rollback when candidate startup fails

### Requirement: Describe supported hosts accurately

The guide SHALL identify Linux amd64/x86_64 and arm64/aarch64 as supported
tunnel-client platforms, including Home Assistant OS and Home Assistant
Container. It SHALL explain that unavailable platform binaries produce an
unsupported-platform repair.

#### Scenario: Choosing a Home Assistant installation type

- **WHEN** an operator checks the host requirements
- **THEN** the guide includes Container alongside Home Assistant OS on supported architectures
- **AND** it describes the repair for a platform without a supported binary

### Requirement: Use a connection that survives maintenance actions

The maintenance guide SHALL recommend the Home Assistant UI or a direct local API
connection for actions that restart the tunnel. It SHALL explain that sending
such an action through the affected tunnel interrupts the carrying request.

#### Scenario: Operator initiates a restart or executable update

- **WHEN** the operator follows the maintenance instructions
- **THEN** they are directed to the Home Assistant UI or a direct local API connection
- **AND** the guide describes the interruption caused by invoking the action through the same tunnel
