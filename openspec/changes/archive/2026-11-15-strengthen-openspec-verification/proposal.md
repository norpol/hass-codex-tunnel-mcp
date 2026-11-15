# Proposal

## Why

Small changes can omit design documents, but scenario-to-test guidance currently
lives only in design rules. Keep verification available on the lightweight path
and distinguish structural validation from evidence that behavior is correct.

## What Changes

- Move scenario/test mapping into task guidance and add concise implementation
  and archive guidance for check results, unresolved gaps, and contract conflicts.
- Generate the six core workflows plus optional verification with OpenSpec 2.0.0.
- Document verification and reproducible regeneration of that workflow selection.

## Capabilities

### New Capabilities

None. This tooling change uses `skip_specs: true`.

### Modified Capabilities

None. Integration behavior and existing contracts remain unchanged.

## Impact

Only project configuration, generated workflows, and contributor documentation
change. The default schema and conditional design documents remain in use.
