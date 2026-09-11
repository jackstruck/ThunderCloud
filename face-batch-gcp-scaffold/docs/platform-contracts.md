# Final platform contracts — deployed 2026-09-10

These contracts describe the schema 024 release. See [production cutover and acceptance](platform-cutover-review.md).

## Ownership and retained evidence

| Structure | Final responsibility | Required change |
| --- | --- | --- |
| `media_run` | All upload, URL and operator archive submissions; policy, selection, state, expiry and source acquisition | Add archive input kind and explicit selection policy (`manual` or `all_tracks`). Archive adapter supplies exact bucket/object/generation. |
| `run_operation` | Durable CPU acquisition, GPU detection/comparison, promotion and deletion work | All adapters enqueue here; retries and leases retain current transactional claim rules. Remove rollout/work-item consumers after transfer or drain. |
| `submission_face_group` | New detection evidence: embedding, model, detector, geometry/timing, quality and generation-pinned previews | Shared detection path writes this for every new input method. |
| `subject_example` | Sole current membership of immutable evidence in a subject | Shared enrollment inserts directly. Corrections update only this membership and derived representations. Keep origin IDs and immutable model/timing/embedding fields. |
| `face_track`, `processing_job` | Required original historical evidence and model provenance | Remove membership authority from tracks; retain job evidence referenced by verified model records. No new archive writer. |
| `subject`, `identity` | Active subject representation and editable identity; existing versions and merge redirects | Canonical embedding, sample count and model derive from examples. Identity/membership changes advance the existing subject version in the correction transaction. |
| `submission_enrollment` | Retired by migration 017 | Keep enrollment outcome/time on the existing face group; ownership and source come from examples. Drop the redundant table without migration audit copies. |
| `enrollment_assignment`, `enrollment_assignment_member` | Explicit new-subject grouping selected by the user | Only new-subject groups. Unattended enrollment supplies one singleton assignment per track. Remove existing-destination fields. |
| `source_asset` | Source attribution and explicit retained storage reference | Add explicit bucket beside existing object/generation. Separate external attribution from storage. Normalize archive generations from source metadata or unique successful work-item evidence for the same URI and digest before retiring the queue. |
| `subject_representative_face` | Gallery image linked to an example and its exact object generation | Use example ownership; remove duplicate subject/source/origin ownership columns after callers migrate. Use the configured gallery bucket and stored exact object/generation. |
| `run_candidate` | Read-only, expiring result snapshot | Add nullable `compared_subject_version`; new comparisons always record it. Preserve stored scores, subject IDs, names and counts. |
| `subject_change_event` | Idempotent correction and review audit | Add dismissal/restore actions using the same actor, request fingerprint, operation ID and before/result conventions. |
| `platform_schema_migration` | Administrative applied-version/checksum ledger | Session lock, atomic step/ledger commit, structural schema fingerprints and verified baseline adoption. No runtime grants. |

An origin structure is retained only where it holds evidence that cannot be removed
without loss. Preserve original model labels and verified equivalence receipts;
comparison uses the verified effective model. Do not overwrite historical labels
or infer equivalence merely from similar dimensions or scores.

## Submission and processing

The existing create/upload-complete/idempotency flow remains the public browser
contract. Operator submission accepts an exact archive storage reference plus an
explicit handling policy and selection policy; bulk creates multiple ordinary
runs. A canonical request fingerprint covers the exact generation and both
policies. Reuse of an idempotency key with different content is a conflict.

Acquisition tooling must atomically persist a protected receipt containing the
idempotency key, content checksum, bucket/object/generation, policies and intended
submitter before handing the acquired object to the run framework. Persist the
acknowledged run ID in that same durable receipt. Retrying a failed handoff reads
the receipt and uses its original key; it must not reupload or create another run.
The run/operation insertion is transactional. A queue invocation failure leaves
the queued operation available for the normal drain to claim.

One enrollment service creates subjects and examples for selected groups; one
correction service performs explicit moves and merges. `search_then_discard`
compares without enrollment; `enroll_only` enrolls without comparison and deletes
source media; `retain_and_enroll` compares and enrolls while retaining the source.
Gallery publication remains generation-pinned and retryable. Gallery repair's
comparison threshold gets a dedicated repair setting; enrollment has no threshold
or automatic identity-association parameters.

## Result presentation

New candidate rows record the subject version compared in the same consistent
read used for ranking. Reading a saved result leaves it unchanged. Display
“Subject changed since this search” when its compared version differs or an
applicable correction event proves a change. With insufficient older evidence,
display that the prior version is unavailable. Continue normal merge navigation
to a survivor while retaining the recorded candidate ID and score in the snapshot.
Source-split mappings are audit evidence, not instructions to attach an old score
to an arbitrary resulting subject. There is no historical membership browser.

## On-demand potential matches

Add `subject_suggestion_dismissal` with:

- `subject_low`, `subject_high`: subject UUID foreign keys and canonical pair primary
  key; require `subject_low < subject_high`.
- `low_version`, `high_version`: positive existing subject row versions.
- `dismissed_by`, `dismissed_at`: authenticated actor and timestamp.
- `restored_by`, `restored_at`: nullable explicit restoration metadata.

A pair is currently dismissed only if neither restoration field is set and both
stored versions equal the current subject versions. Old dismissal history remains
in the audit log. An unrelated edit that changes a subject's version intentionally
makes the pair reviewable again. No separate representation version is added.

| Endpoint | Contract |
| --- | --- |
| `GET /api/subjects/<id>/potential-matches` | Read-only snapshot with requested subject UUID/version and up to 10 candidates. Exact cosine similarity, descending score then UUID. Exclude self, merged/empty/incompatible models and current dismissals before the limit. `dismissed=true` selects currently dismissed pairs for review. |
| `PUT /api/subjects/<id>/potential-matches/<candidate>/dismissal` | Body: `version`, `target_version`, `operation_id`. Canonicalize IDs and versions together, lock the two subjects in UUID order, reject stale/merged state, audit and idempotently dismiss. |
| `DELETE /api/subjects/<id>/potential-matches/<candidate>/dismissal` | Same version and operation checks; explicitly restore, idempotently. |
| Existing combine/move endpoints | Remain the only membership corrections. Potential-match inspection reuses source-grouped merge preview, survivor selection and explicit confirmation. |

Batch candidate names, gallery and example/source counts through the existing
summary infrastructure. Return full UUID, existing version and similarity for
each candidate. A candidate with no gallery remains inspectable by source/examples.
Use existing authentication, origin protection and feature availability. Stale
mutations return 409 and trigger refreshed comparison. After a merge navigate to
the survivor and discard affected suggestions; after a move compare only after
both subjects' representations have been recalculated in the audited transaction.

## Verification and cutover prerequisites

Fresh and upgraded databases must have the same structural fingerprint and shared
runtime grants. Structural equality alone does not prove data preservation. The
rehearsal must separately compare membership, embeddings, effective provenance,
source storage references, gallery ownership and audit mappings on a protected
copy. Queue inventory and an observed submission pause are required before apply.
A reviewed manifest must bind target identity, image/build and migration checksums,
starting schema, backup receipt and final verification invariants. The fresh-copy rehearsal and production apply passed on 2026-09-10, including
all preservation checks, schema equality and application grants. Live/browser
acceptance status is tracked separately in the cutover results.


## Saved-result version implementation

New search comparisons capture `subject.row_version` alongside the score in one
repeatable-read transaction. Saved candidates require that version at commit.
`retain_and_enroll` compares compatible subjects and independently enrolls new
subjects; `enroll_only` skips comparison.

Result reads return `compared_subject_version` (nullable for older rows) and
`subject_version_status`: `unchanged`, `changed`, or `unavailable`. A known version
is checked against the current subject. An older result is marked changed only
when a subsequent edit/move/merge or retained source-split receipt identifies that
subject; otherwise its prior version is unavailable. Reading results never rewrites
recorded IDs, names, scores or counts. Subject links retain the recorded UUID and
use the existing merge redirect when opened. Split evidence does not assign an
old score to a new subject.
