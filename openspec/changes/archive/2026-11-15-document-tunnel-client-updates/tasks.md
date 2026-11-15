# Tasks

## 1. Updater contract and evidence

- [x] 1.1 Write four behavior requirements and map every scenario to code/tests below; pass strict change validation and review the readiness and rollback limitations against implementation.
- [x] 1.2 Add focused coverage for missing digests, readiness before activation, and rollback failure reporting; run the updater, manual-control, scheduler, binary, and tunnel tests with the pinned Home Assistant dependencies.
- [x] 1.3 Add the updater capability to README and project context; verify links and keep operator instructions and existing baseline contracts unchanged.

## 2. Integrated verification

- [x] 2.1 Run the full available regression suite, strict OpenSpec validation, scenario/test-reference checks, and diff/privacy review; record results and any skipped external checks.

## Scenario evidence

Implementation: `custom_components/hass_codex_tunnel_mcp/` contains release
selection and downloaded-archive checks in `binary.py`, update state/recovery and
timers in `updater.py`, one-shot UI controls in `button.py`/`select.py`, and entry
unload ordering in `__init__.py`. Test references below are relative to `tests/`.

| Scenario | Automated evidence |
| --- | --- |
| A release reaches the age boundary | `test_manual_update_controls.py::test_v020_exact_hold_boundary`; `test_binary.py::test_release_selection_skips_young_and_suffix_versions`; `test_updater_scheduler.py::test_scheduled_updates_preserve_eligibility_and_rollback` |
| An operator selects an early release | `test_manual_update_controls.py::test_selection_does_not_change_automatic_or_default_manual_updates`, `test_automation_cannot_request_age_override`, `test_entry_controls_select_then_install_once_and_reset_after_failure`, `test_manual_early_update_uses_readiness_and_rollback` |
| A selected release is no longer valid | `test_manual_update_controls.py::test_manual_rejects_unpublished_nonstable_and_nonupgrades` |
| Verification cannot establish archive integrity | `test_binary.py::test_install_from_zip_bytes_rejects_bad_checksum`, `test_bad_download_preserves_installed_binary`; `test_manual_update_controls.py::test_missing_digest_prevents_candidate_installation`, `test_manual_override_never_bypasses_checksum`; `test_updater_scheduler.py::test_scheduled_updates_preserve_eligibility_and_rollback` |
| A candidate becomes ready | `test_manual_update_controls.py::test_candidate_is_active_only_after_readiness`; `test_updater_scheduler.py::test_scheduled_updates_preserve_eligibility_and_rollback`; `test_tunnel.py::test_readiness_tracks_http_probe_not_health_file` |
| Candidate startup fails | `test_manual_update_controls.py::test_manual_early_update_uses_readiness_and_rollback`; `test_updater_scheduler.py::test_scheduled_updates_preserve_eligibility_and_rollback` |
| Recovery also fails | `test_manual_update_controls.py::test_failed_update_reports_failed_recovery` |
| Reload follows a recent check | `test_updater_scheduler.py::test_recent_deadline_survives_repeated_reloads`, `test_nearly_due_check_gets_minimum_startup_delay` |
| There is no usable recent check | `test_updater_scheduler.py::test_missing_overdue_or_invalid_check_runs_after_startup`, `test_check_waits_for_home_assistant_to_finish_starting` |
| A check fails and the entry reloads | `test_updater_scheduler.py::test_failed_startup_check_keeps_retry_deadline_after_reload`, `test_failed_tick_does_not_stop_future_checks_or_other_entries` |
| Automatic updates are disabled | `test_updater_scheduler.py::test_disabled_startup_check_never_fetches_or_installs`, `test_entries_have_independent_options_and_unload` |
| An entry unloads during scheduled work | `test_updater_scheduler.py::test_unload_cancels_pending_startup_check`, `test_unload_cancels_running_check_without_rescheduling`, `test_unload_during_update_waits_for_old_process_cleanup`, `test_cancelled_install_keeps_shared_lock_until_files_are_ready`, `test_entries_have_independent_options_and_unload` |

The optional real-binary tests `test_release_compatibility.py::test_release_automatic_update_and_rollback`
and `test_release_control_plane_auth_failure_is_diagnostic` exercise update and
readiness limitations on Linux amd64 and arm64 in CI. They do not prove a live
Home Assistant deployment's end-to-end authentication. The ephemeral selection
on reload is also checked by source review: new updater instances start with no
selection and persisted state excludes it. No new runtime guarantee is inferred
from documentation alone.

## Verification results

- With Python 3.14.10 and `requirements-test.txt` installed, `python -m pytest -q`
  passed 255 tests, including five new cases. The 39 real-binary cases were
  skipped locally because release archives were absent; both CI architecture
  jobs provide those fixtures. `python -m compileall -q custom_components tests`
  also passed.
- Strict change and baseline validation passed. All 12 scenario rows have valid
  test references (32 references checked against test definitions), relative
  README links resolve, and the new baseline matches the delta.
- Advisory review mapped all four requirements to existing runtime behavior;
  readiness does not claim end-to-end authentication, checksum verification is
  scoped to downloaded archives, and recovery is an attempt that can fail.
  Optional design adherence is not verified because no design was needed.
- Runtime files, both earlier baseline specs, and operator documentation are
  unchanged. Diff and artifact review found no whitespace errors, secrets, or
  private data. Live deployment checks were not performed.

## Workflow follow-up

- Sync the verified delta, archive this change, and validate the baseline and completed archive tasks.
- Commit separately from verification tooling and check both architecture CI jobs on the PR.
