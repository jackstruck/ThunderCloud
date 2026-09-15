# Within-source bulk matching — 2026-09-13

Implementation is complete and deployed. The initial proposal-only release below
was followed by the reviewed operator rollout documented at the end of this record. Unattended
application is not implemented or enabled. No production subjects were merged.

## Release and access

- Console image: `us-central1-docker.pkg.dev/teak-banner-dome/face-batch-worker/console@sha256:2fd9e4b6fee0b78e35d9283cdc7d2ee3fe3c8651d8e7e9f020e5c0ce6c681aa9`.
- Console: `https://face-console-4nq5bomqgq-uc.a.run.app`.
- Ready revision: `face-console-00028-5v8`, serving 100% of traffic. Final served JavaScript/CSS and container runtime-file hashes match the tested worktree. The final revision had no error-severity log entries during verification.
- Terraform records the image and `source_merges_apply_enabled=false`.
- CLI identity: `face-batch-merge-cli@teak-banner-dome.iam.gserviceaccount.com`.
  This keyless account has console IAP access only; the existing approved console
  principal may sign its short-lived request JWTs. No database/storage/job roles
  or service-account keys were granted. IAM signing audit logs identify the caller.
- IAP's existing user membership remains. No programmatic OAuth allowlist was added:
  the shared SDK client is rejected by the organization restriction, so the CLI uses
  service-account JWT authentication instead.
- Console updates were scoped directly because Terraform also proposed pre-existing
  registry cleanup-policy and ingestion-image drift. Those unrelated resources were
  not changed. State was refreshed after the scoped release.

## Acceptance evidence

`PYTHONPATH=. FACE_SUBJECT_TEST_PORT=55439 FACE_BROWSER_TESTS=1 .test-venv/bin/pytest tests/unit tests/integration/test_subject_management.py`
passed **216 tests** against an isolated PostgreSQL 17/pgvector instance, including
Chromium browser flows. Ruff, JavaScript syntax, OpenAPI YAML parsing, and Terraform
validation also passed. Protected detailed artifacts are under `.local/source-merges*`.

| Requirement | Evidence |
| --- | --- |
| Distinct within-source groups; unrelated/cross-source lookalikes excluded | `test_source_groups_scope_replay_partial_resume_and_recovery` |
| Complete linkage prevents weak A–B–C chains; stable tie handling | `test_complete_linkage_distinct_groups_chain_dismissals_and_stable_ties`, randomized comparison with a naive reference |
| Current source membership, not historical origin, controls eligibility | `test_source_boundary_rechecked_and_multisource_skipped` |
| Models, invalid/missing embeddings, current dismissals, identity conflicts | `test_source_missing_models_limits_and_read_only_rollout`, `test_source_chain_dismissals_identity_and_forged_score`, model partition unit test |
| Entire group revalidated under the enrollment lock | `test_source_recheck_waits_for_enrollment_lock_then_rejects_entire_group`, `test_source_identity_changes_and_concurrent_manual_merge_are_stale` |
| Interrupted responses and partial completion retain original IDs | CLI lost-response and partial-resume test; database replay/resume test |
| Different admitted payload conflicts even after a definitive failure | Identity-conflict replay test; durable failed-operation event |
| Mutation failure rolls back all members before recording outcome | `test_source_merge_rolls_back_mutation_and_persists_definitive_failure` |
| Attribution preserved; eligible merge members can be separated | Database source-group recovery test and existing merge/separation regression suite |
| CLI and UI apply through the same authenticated service | `test_browser_source_matching_mobile_and_cli_parity` |
| Member exclusion, pair dismissal, direct merge without modal | `test_browser_source_group_exclusion_and_dismissal` |
| Multiple sources scanned independently; mobile usable | Browser multi-source test and live desktop/mobile captures |
| Oversized sources fail without partial plans; groups over 50 stay intact | Limit test and `test_source_oversized_group_never_split_or_applied` |
| Bounded deterministic work | 400 subjects / 79,800 identical-score pairs clustered in 0.642 seconds locally; result stayed one oversized group |
| Private plans, durable receipts, immutable retries | CLI unit tests and live CLI planning with keyless IAP authentication |
| Authenticated production proposal rollout, apply gate off | Live smoke report: authenticated features/proposals 200; apply 403 `proposals_only`; unauthenticated request redirected to sign-in |

The initial exploratory live smoke scanned three explicit sources with 13, 14, and
102 eligible subjects; it returned 0, 0, and 2 groups at an explicitly supplied 0.9
threshold. **This is an exploratory test configuration, not an accepted or calibrated
threshold.** Live browser checks showed both groups, a disabled apply control, no
JavaScript errors, and no horizontal overflow at 390px or 1440px. Screenshots and
private plans are retained locally for review.

## Reviewed operator rollout and clearer group UI

The user reviewed source `2f8d380a-1391-5f4b-9a8a-1b0f628bc48e` with cosine `0.5`:
all eight groups combine subjects well, while some separate groups still depict the
same person. A fresh scan confirmed eight groups covering 35 of 44 eligible subjects
under model `adaface-ir18-6b6a3577`. Applying all eight groups would leave 17 subjects.
No production merge was submitted by the implementation agent.

This group-level review satisfies the operator rollout gate. Exact missed group
pairs were not supplied, so the missed-match observation is recorded at source level;
no pair-level precision/recall or false-merge rate is inferred. The assessment is in
`.local/source-merges-reviewed/review.json`, with its contemporaneous plan.
`source_merges_apply_enabled=true` is now configured. Unattended application remains
unimplemented and disabled.

The clearer UI uses numbered groups, subject-count summaries, larger single previews,
a marked survivor, expandable identity details, select-all/clear controls, and an
explicit merge selection summary. The old “Incorrect pair” area is now a collapsed
“Fix a mismatch” section with the action “These are different people.”

Release image: `us-central1-docker.pkg.dev/teak-banner-dome/face-batch-worker/console@sha256:6318230027f03df3a0f9bf1f5ad3f5a0c113c646e7d95c7fb3b2ec01f4e1b89a`.
The three affected browser flows passed after this UI change, including mobile review,
CLI parity, group selection counts, exclusion and pair dismissal. The prior full
216-test acceptance evidence continues to cover the unchanged matching/merge service.

Revision `face-console-00030-5ql` initially served 100% of traffic. Live verification confirmed
the deployed JavaScript and CSS match the reviewed files, all eight groups render,
selection enables the merge action, and the mobile layout has no horizontal overflow
or JavaScript errors. The authenticated apply endpoint accepted an unselected plan
and skipped every group, verifying the enabled route without changing subjects.

### Saved-review application status fix

Saved reviews retained `apply_enabled=false` from the proposal-only rollout, leaving
the merge button disabled even with selected groups. The panel now fetches the current
application gate from `/api/features` when it opens, preserving group selections,
survivors, and operation IDs. A failed status check keeps the action disabled and
shows a reload instruction. The service continues to enforce its own application gate.

Revision `face-console-00031-wst` initially served 100% of traffic using image digest
`sha256:6a9dfcf6cd572b2fd5166f82eff2082617354de5e3c521723f20c59e8a8df147`.
Three browser flows and twelve matching/CLI unit tests passed. The regression covers
restoring selected proposal-only groups with the server enabled, and disabling the
action when the server gate is off.
Live browser verification restored an old disabled plan with all eight groups selected:
the button enabled and every group payload remained unchanged. No merge was submitted.

### Collapsible matching panel

The matching panel now starts collapsed on initial load, including restored reviews.
After all selected groups merge successfully it collapses and displays the merged
group count in its summary. Partial failures and unresolved outcomes stay open.
The panel remains expandable to inspect saved results. Three affected browser flows
passed, including initial/reload collapse and automatic collapse after a real test merge.

Revision `face-console-00032-9cd` serves 100% of traffic with image digest
`sha256:d39cdd89882e97f770c42ae7420b519b7123f46e210ee457b6f224fba693813a`.
Live verification on the reviewed source confirmed initial collapse, opening by
click, collapse on reload, and no mobile overflow. No production merge was submitted.
