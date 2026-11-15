# Spec Delta

## Purpose

Define the installation and maintenance guidance for this custom integration,
including Home Assistant Marketplace and existing HACS installations.

## ADDED Requirements

### Requirement: Lead with Marketplace installation

The installation guide SHALL lead with Home Assistant 2026.11 and newer using
Settings > Marketplace. It SHALL identify this repository as a custom integration
and provide its GitHub URL for Add from link, the required GitHub connection,
installation, and subsequent integration configuration.

#### Scenario: New Marketplace installation

- **WHEN** an administrator follows the guide on Home Assistant 2026.11 or newer
- **THEN** the guide directs them to add the repository link and complete the GitHub connection
- **AND** it explains installation, any restart prompt, and setup through Devices & services

### Requirement: Explain HACS migration and legacy installation

The guide SHALL explain that Home Assistant migrates existing HACS installations
to Marketplace, including custom repositories and update entities. It SHALL retain
a brief HACS installation path for supported Home Assistant versions before
2026.11 and link the official Marketplace documentation.

#### Scenario: Existing HACS installation moves to Marketplace

- **WHEN** an operator upgrades an existing HACS installation to Home Assistant 2026.11
- **THEN** the guide explains that Home Assistant carries the installation over
- **AND** it does not direct the operator to add the same repository again

#### Scenario: Operator uses an older supported Home Assistant version

- **WHEN** the operator follows the legacy installation instructions
- **THEN** the guide describes adding a HACS custom repository with category Integration
- **AND** it explains installation, restarting Home Assistant, and adding the integration

### Requirement: Distinguish integration updates from executable updates

The maintenance guide SHALL distinguish Marketplace updates to the integration
code from the integration's own tunnel-client updater. It SHALL explain that
Marketplace uses hacs.json metadata and does not control the executable's
eight-day release hold, manual override, or rollback policy.

#### Scenario: Updating integration code

- **WHEN** an operator wants to update this repository's integration code
- **THEN** the guide directs them to the Marketplace update entity under Settings > Updates
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

### Requirement: Explain custom-integration status

The guide SHALL explain that Marketplace installation retains this project's
custom-integration status and that Home Assistant's untested-custom-integration
warning is expected.

#### Scenario: Loader warning appears after installation

- **WHEN** Home Assistant warns that this custom integration has not been tested by Home Assistant
- **THEN** the troubleshooting guide explains the warning without presenting it as a tunnel startup failure

### Requirement: Use a connection that survives maintenance actions

The maintenance guide SHALL recommend the Home Assistant UI or a direct local API
connection for actions that restart the tunnel. It SHALL explain that sending
such an action through the affected tunnel interrupts the carrying request.

#### Scenario: Operator initiates a restart or executable update

- **WHEN** the operator follows the maintenance instructions
- **THEN** they are directed to the Home Assistant UI or a direct local API connection
- **AND** the guide describes the interruption caused by invoking the action through the same tunnel
