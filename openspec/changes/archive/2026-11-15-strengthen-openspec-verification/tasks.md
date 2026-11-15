# Tasks

## 1. Verification guidance and workflows

- [x] 1.1 Move scenario/test mapping into tasks and add apply/archive guidance; inspect CLI instructions to confirm each rule reaches its intended workflow.
- [x] 1.2 Generate core plus verification with OpenSpec 2.0.0 and document regeneration; verify a second forced regeneration produces identical files.
- [x] 1.3 Document structural validation, tests, and advisory verification; review the generated verification workflow against this change with specs intentionally skipped and design omitted.

## 2. Integrated checks

- [x] 2.1 Pass strict OpenSpec validation and diff/privacy checks; confirm this no-spec, no-design change remains apply-ready and existing baseline specs are unchanged.

## Verification results

- CLI `instructions tasks`, `instructions apply`, and `instructions archive`
  expose the expected mapping rule and operation guidance. Apply is ready without
  specs or design; strict validation passes.
- Two forced generations produce identical files; a forced dry run reports no
  changes. All six existing generated workflows and both baseline specs are
  unchanged. The seventh workflow retains upstream metadata and the MIT notice.
- Advisory review found the delivered configuration and README consistent with
  the proposal. Spec checks are not applicable (`skip_specs`). Design adherence
  is not verified because the optional design was omitted; no design is needed
  for this tooling change. No runtime behavior changed or runtime tests added.
- Diff and artifact review found no whitespace errors, secrets, or private data.

## Workflow follow-up

- Archive the completed tooling change and validate archived tasks.
- Commit it separately from the updater contract.
