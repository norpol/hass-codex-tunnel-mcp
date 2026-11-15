# Spec Delta

## Purpose

Define how the tunnel integration handles MCP endpoint availability during setup,
including automatic recovery and diagnostic messages that protect credentials.

A **retryable probe failure** means a handled HTTP 404/server error, timeout, or
connection failure. Unexpected setup exceptions are outside this definition.

## ADDED Requirements

### Requirement: Retry unavailable MCP endpoints

The integration SHALL defer setup and use Home Assistant's automatic retry for
retryable probe failures: after startup finishes for failures during startup,
or with Home Assistant's retry backoff when already running.

#### Scenario: MCP route appears after Home Assistant starts

- **GIVEN** the MCP server has not registered its route during Home Assistant startup
- **WHEN** the initial probe receives HTTP 404
- **THEN** setup remains pending and retries automatically after startup
- **AND** a subsequent reachable response permits tunnel setup to continue

#### Scenario: Endpoint remains unavailable

- **WHEN** a retryable probe failure occurs after startup
- **THEN** setup remains pending with a retry reason and another retry is scheduled
- **AND** HTTP 404 is never treated as successful reachability

### Requirement: Keep failed setup attempts isolated

A retryable probe failure SHALL leave no active tunnel, diagnostic entities, or
automatic update schedule for the failed attempt. The integration SHALL discard
that attempt's incomplete runtime state without removing another entry's runtime.

#### Scenario: One entry cannot reach its MCP endpoint

- **GIVEN** another tunnel entry already has runtime state
- **WHEN** this entry encounters a retryable probe failure
- **THEN** this attempt starts no subprocess, entities, or update schedule
- **AND** its incomplete runtime state is removed while the other entry is retained

### Requirement: Report probe failures according to startup state

For a retryable probe failure, the integration SHALL create an unreachable-URL
repair only while Home Assistant is fully running. During startup it SHALL leave
setup pending without creating startup-failed or unreachable-URL repairs. It
SHALL NOT emit its own error-level traceback for these failures.

#### Scenario: Temporary startup 404

- **WHEN** the MCP endpoint returns HTTP 404 while Home Assistant is starting
- **THEN** the integration creates neither failure repair for that attempt
- **AND** Home Assistant reports the pending retry without a duplicate integration error

#### Scenario: Persistent failure is actionable

- **WHEN** a retryable probe failure occurs after Home Assistant has finished starting
- **THEN** an unreachable-URL repair identifies an HTTP status, timeout, or connection failure
- **AND** it tells the operator that retries continue and to check the MCP server and URL

### Requirement: Separate reachability from authentication

The integration SHALL probe with GET and include a bearer Authorization header
when a token is configured. It SHALL accept successful responses and HTTP 401,
403, and 405 as reachability evidence without treating that evidence as proof of
working authentication or a successful MCP request.

#### Scenario: MCP endpoint does not support GET

- **WHEN** the endpoint returns HTTP 405 to the GET probe
- **THEN** the probe permits tunnel setup to continue

#### Scenario: Endpoint responds with an authentication challenge

- **WHEN** the endpoint returns HTTP 401 or 403
- **THEN** the probe establishes only that the endpoint responds
- **AND** successful authentication and MCP operation still require separate validation

#### Scenario: Configured token accompanies the probe

- **WHEN** the operator has configured a Home Assistant access token
- **THEN** the probe sends that token in the bearer Authorization header
- **AND** a probe without a configured token omits that header

### Requirement: Sanitize expected probe diagnostics

Retry messages and displayed exception chains for retryable probe failures SHALL
omit credentials, secret URL paths and query values, and raw underlying exception
details. The unreachable-URL repair SHALL redact the endpoint path and query.

#### Scenario: Handled probe failure contains private details

- **WHEN** a retryable probe failure contains a secret URL or arbitrary private detail
- **THEN** the retry message and exception chain expose only the sanitized failure reason
- **AND** the repair omits secret path and query content

### Requirement: Clear failure repairs on recovery

Successful setup after a retryable probe failure SHALL clear prior startup-failed
and unreachable-URL repairs and establish the tunnel's diagnostic entities.

#### Scenario: A persistent failure recovers

- **GIVEN** earlier failed attempts have left failure repairs
- **WHEN** a retry reaches the endpoint and tunnel startup succeeds
- **THEN** the entry becomes loaded and those failure repairs are removed
- **AND** the successful setup establishes its entities once

### Requirement: Preserve visibility of other setup errors

Invalid MCP URL configuration SHALL remain visible as a repair even during Home
Assistant startup. Unexpected tunnel startup errors SHALL continue to produce an
error log and a startup-failed repair.

#### Scenario: Invalid persisted URL

- **WHEN** setup encounters an invalid MCP URL during startup
- **THEN** it reports the configuration problem as a repair without probing or starting a tunnel

#### Scenario: Unexpected tunnel startup exception

- **WHEN** tunnel startup raises an unexpected exception after the endpoint probe succeeds
- **THEN** the integration logs the startup failure and creates a startup-failed repair
