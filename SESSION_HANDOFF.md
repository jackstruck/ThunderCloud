codex resume 01a09cbd-e79f-7fd2-81ba-7d807c6b94e8
# ThunderCloud session handoff

Updated 2026-09-18 UTC. This records the session ending after automatic enrollment
selection and cosine 0.4 were deployed. Deployment observations below were made on
2026-09-17; recheck live state before claiming current run or job status.

## Start here

1. Read this file, `git status --short`, and the relevant dated release notes under
   `data-batch-gcp-scaffold/docs/history/`.
2. Preserve the working tree. It contains substantial **uncommitted, deployed work**
   from this session, including untracked modules, migrations/config changes, tests,
   and documentation. Do not reset, clean, stash indiscriminately, or replace it
   with HEAD. The user has not requested commits.
3. Work primarily in `data-batch-gcp-scaffold/`; `bulk-download/` is a separate
   acquisition package in the same repository. Repository root is
   `/workspaces/ThunderCloud`.
4. If continuing operational monitoring, check the two retried runs and the longer
   historical matching pass listed below. Their final outcomes are not yet verified.
5. Distinguish new-source enrollment, historical source matching, gallery search,
   and acquisition. They use different jobs and settings.

No unfinished code/deployment request remained when this handoff was written.
There are operational follow-ups, not permission to start new mass merges.

## Working norms and user preferences

- Act on requests such as “implement,” “solve,” and “deploy”; finish the authorized
  work, test appropriately, deploy when requested, and verify the live result.
  Avoid repeatedly asking for approval already given. Ask only for genuinely missing
  information or authorization; do not invent approval requirements.
- Provide concise progress updates during longer work and a short, self-contained
  final answer. Clearly distinguish deployed, queued, fetching, and completed.
- Preserve existing examples when merging subjects. The user explicitly said
  examples can be kept, just merged. Do not force a fixed subject count per source.
- UI language should be provider-neutral. Do not add “Hotscope” or other provider
  explanations to ordinary forms and helper labels. Operational docs can name providers.
- The user favors fewer labels and clicks, full-width primary actions, and paired
  secondary controls sharing a row. Do not restore removed helper text or reason labels.
- Never expose credentials, signed media URLs, tokens, or private diagnostics in
  public/tracked files. Do not send messages to others without explicit authorization.
- Follow current session instructions over this historical note. No `AGENTS.md` was
  found during this session. Do not spawn agents unless current instructions authorize it.
- Prefer scoped searches. `rg` was unavailable here; `grep`/`find` were used instead.
- Keep changes scoped. Image-only deployments were used to avoid unrelated Terraform
  drift (notably label changes from `face-batch` to `data-batch`). Review plans first.

## Current product behavior

### Submission and enrollment

Latest deployed behavior for **new submissions**:

- `search_then_discard`: manual group selection by default, then search.
- `retain_and_enroll`: automatically select all detected groups, search, enroll,
  and perform within-source automatic grouping.
- `enroll_only`: automatically select all detected groups and enroll without gallery
  search; perform within-source automatic grouping. Derived records/source attribution
  survive, while submitted media follows the existing cleanup policy.
- `RunRepository.create` persists `all_tracks` for both enrollment policies, even if
  an operator payload asks for `manual`. Search retains its prior default and supports
  explicitly unattended operator/batch searches.
- Existing paused runs were **not migrated** to automatic selection. Their stored
  policies/selections remain authoritative.
- Automatic new-source matching now uses cosine **0.4**, configured by
  `FACE_SOURCE_MERGES_AUTO_THRESHOLD` on `face-interactive-gpu` and local Terraform
  `source_merges_auto_threshold = 0.4`.
- This is source-local complete-linkage grouping, not a change to gallery search or
  face detector thresholds. Complete linkage prevents a chain of similar pairs from
  merging when another pair in the group fails the threshold.
- Preserve examples, representative images, attribution, and source boundaries.
  Existing safeguards include exclusions, dismissed pairs, identity/model conflicts,
  a 50-member group cap, and new-source planning bounds of 400 subjects/10 seconds.
  Bounds can leave enrollment successful with a manual-review report.
- Exact-content duplicate enrollment can reuse prior enrollment. Reuse does not
  rerun automatic grouping or reactivate excluded/disabled content.

Key files: `worker/run_repository.py`, `worker/interactive_repository.py`,
`worker/source_merges.py`, `worker/subject_management.py`, `ui/index.html`.

### Source acquisition

- Link UI accepts user profile links such as `/user/Mary` and submits every video.
  Batch processing automatically selects all detected groups.
- Text-file link input is local-only and was removed from the browser UI.
- `luluvdo.com`, `luluvdoo.com`, `luluvid.com`, and their `www` forms are accepted
  by the UI ingestion resolver, including links reached through JustPaste.
  Aliases resolve through canonical `luluvid.com` pages.
- Luluvid/JustPaste HLS is acquired using the bounded MPEG-TS downloader, remuxed to
  MP4, and fully decoded/duration-validated. Matching provider embed URLs supply
  playback referers. Every segment/rendition/redirect remains on the resolved media
  hostname and uses connection-time public-address validation. Hotscope retains its
  fixed CDN host restriction.
- Key files: `worker/source_adapters.py`, `worker/hls_fetch.py`,
  `worker/secure_fetch.py`, `worker/ingest_drain.py`.
- `ingest_drain` currently turns `ValueError` into nonretryable `source_rejected`
  and other handled failures into retryable `fetch_failed`; the UI error is generic.
  It does not preserve the detailed exception message. Diagnose through saved source
  records and controlled reproduction rather than guessing from the generic message.

### UI changes already completed

- Group matching collapses initially and after successful merge.
- Group selection summary and merge action sit immediately below Select all groups /
  Clear group selection.
- Manual subject selection has Select all / Clear selection in one row and a
  full-width merge button beneath.
- Disable / Restore each occupy half the full row. No “not a subject” versus
  “unusable data” selector; compatibility backend reasons remain.
- “Include excluded” became “Show excluded.” Redundant merge/range-selection labels
  were removed.
- Start link search and Upload and search buttons are full width.
- Runs with no candidates still link to their source when available.

## Deployment inventory (last verified 2026-09-17)

Project: `teak-banner-dome`; region: `us-central1`.
Console/IAP origin: `https://face-console-4nq5bomqgq-uc.a.run.app`.
Artifact prefix: `us-central1-docker.pkg.dev/teak-banner-dome/face-batch-worker/`.

| Runtime | Image repository and digest | Notes |
| --- | --- | --- |
| `face-console`, `face-ingest-drain` | `console@sha256:336142c8050743344341205dc21924ebe2d4ad8b3cd1003e0ce2902abde39468` | Console revision `face-console-00049-sv4`, 100% traffic |
| `face-interactive-gpu` | `worker@sha256:77f71e63f03cd14bc2aa57859a6c3ba67b23c913020c79b4770d157baef7c90e` | Image unchanged; automatic source threshold now `0.4` |
| `face-acquisition-batch` | `acquisition-batch@sha256:44419806666f4323e5409375c6791226765a12f16678e1c161b1ef63a510961a` | Acquisition verification fixes |
| `face-source-merge-backfill` | `source-merge-backfill@sha256:7d5ae417c7ea8872cd72a687816e92327b6e78be12dd0dacd124b319c3825710` | Longer historical pass; separate threshold/scope |

Ignored `terraform/terraform.tfvars` matches these deployed image references and
retains prior digests for rollback. Do not print the entire file unnecessarily.
Console releases layered only changed modules/UI onto known deployed images;
Dockerfiles/build/deploy evidence are under private `.local/` directories.

## Operational follow-ups

### Two Luluvid runs

- `254e4e60-1eec-432d-a5c3-1259bb58f184` → `https://luluvdo.com/pplp3upt5x6m`
- `052d9300-dd8b-419e-9244-264179d89514` → `https://luluvdo.com/vj6ocvictun5`

Original failure: missing hostname adapter caused HTML to be fetched as direct
media, producing “response has an unsupported content type.” Both links subsequently
passed complete download/validation in the release container and a Cloud Run smoke
job (11,827,030 and 6,018,546 bytes respectively).

At the user's explicit request, both existing run IDs were requeued through the
normal retry API at approximately 21:50 UTC on 2026-09-17 with `retain_and_enroll`.
Last verified state: **fetching**, not completed. Both had stored `manual` selection
and were requeued before the automatic-selection deployment. They may therefore
pause for selection; do not claim they were automatically migrated or finished.
Private evidence/scripts: `.local/luluvdo-support/`. Temporary diagnostic/smoke/retry
jobs were deleted after use. Do not rerun old scripts blindly: they have state guards
and some refer to deleted temporary jobs.

### Historical matching

Original 0.5 pass:

- Backfill ID `f895dca4-ffd8-4466-91b3-47381d7764e6`.
- Cutoff `2026-09-16T11:44:08.088520+00:00`.
- Execution `face-source-merge-backfill-snl7d`, completed 2026-09-17 03:37:41 UTC.
- 2,967 selected sources: 1,901 completed, 656 dynamically skipped due to prior
  merges, 410 marked manual review/time budget; another 84 were initially excluded.
- 6,334 groups merged, 12,685 subjects combined, 1,675 sources changed; all examples
  preserved and no reported errors.

The user authorized a longer pass for the 410 time-limited sources:

- Backfill ID `495dc6b4-58e1-4e2a-99c9-d36120ffeea7`.
- Execution `face-source-merge-backfill-sc64j`.
- 120-second scan budget; cosine **0.5**, 400-subject/50-member caps unchanged.
- Final outcome **not verified in this session**. Read the report before reporting progress.
- Reports live in bucket `teak-banner-dome-bulk-videos` at
  `batch-control/hotscope-20260914/historical-matching/<backfill-id>.json`.
- The later 0.4 request changed new-source enrollment only. It did not launch a new
  historical sweep, modify this running pass, or remerge manually reviewed sources.
- The original `.local/historical-matching/control.py read` reads the original report,
  not necessarily the longer-pass report. `.local/long-scan/` has the latter evidence.

User expects typically 1–4 subjects per source. Suggested, but **not implemented or
separately authorized**, further approaches included examining the distribution after
this pass, reviewing another pass for automatically merged sources, and evaluating
example-level matching. Do not force groups into that expected count.

Historical count snapshot, 2026-09-17 06:50:59 UTC (not current): 36,607 unmerged
subjects, 36,605 matching-eligible, 3,465 nondeleted sources, 3,373 sources with examples,
71,379 examples. Aggregate ratio alone does not prove all remaining subjects are duplicates.

### Batch with nine failed acquisitions

Batch `15372e4f-5afa-5f63-97fb-df8f48d77708`, policy `retain_and_enroll`, 150 videos.
Nine formerly failed IDs: `3eCWz`, `3usjG`, `AfBei`, `3pALY`, `mhOgo`, `A00jU`,
`6QPB2`, `3CBrh`, `ChbeX`.

Resolved and deployed: eight exact-media archives differed only in MP4 container
bytes between FFmpeg versions; the ninth had reserved H.264 color metadata rejected
by newer FFmpeg. Existing archives now require immutable-generation/CSEK/metadata
checks plus strict compressed-packet/timing/stream fingerprints before reuse. A
temporary metadata-normalized copy is used for the specific color-tag validation
case; original media is preserved. Different media is rejected, never overwritten.

Retry execution `face-acquisition-batch-m4zd8` acquired all nine. Last verified batch
snapshot: **150/150 acquired**, zero acquisition failures, 149 runs succeeded and one
in progress. Final processing completion was not subsequently checked.
State path: `batch-control/hotscope-20260914/profiles/15372e4f-5afa-5f63-97fb-df8f48d77708.json`.
Evidence: `.local/acquisition-fix/` and dated acquisition verification note.

## Tests and operational access

- Python environment: `data-batch-gcp-scaffold/.test-venv/`.
- Run integration tests with `.test-venv/bin/python -m pytest` from the scaffold
  directory so the local `maintenance` package is importable. Direct pytest invocation
  previously failed collection with `ModuleNotFoundError: maintenance`.
- Subject integration tests require an isolated `pgvector/pgvector:pg17` database,
  `POSTGRES_HOST_AUTH_METHOD=trust`, localhost-only random port, and
  `FACE_SUBJECT_TEST_PORT=<port>`. **They truncate data: never point at production.**
  Example: `docker run -d --rm --name <unique-test-name> -e POSTGRES_HOST_AUTH_METHOD=trust
  -p 127.0.0.1::5432 pgvector/pgvector:pg17`; inspect `docker port` then stop afterward.
- Browser cases require `FACE_BROWSER_TESTS=1`; some older selectors were stale after
  intentional UI simplification. Do not undo product changes to satisfy stale selectors.
- Last enrollment deployment: 11 focused real-DB integration cases + 32 unit cases
  passed, along with Ruff on changed worker code and `git diff --check`. Included
  URL/upload policy transitions, all-example preservation, 0.45 pair merging at 0.4,
  idempotency, unattended archive processing and atomic automatic merge retry.
- Luluvid release: 45 focused tests, local/release-container acquisition, and production
  Cloud Run smoke acquisition of both links passed.
- Acquisition release: 33 downloader tests plus 3 subtests passed.
- Prior disable/restore UI was checked with Playwright at 390px and 1280px.

Cloud access details (identifiers only; no credentials in this file):

- SDK executable from scaffold: `../google-cloud-sdk/bin/gcloud`.
- ADC works via `google.auth.default(scopes=['https://www.googleapis.com/auth/cloud-platform'])`
  and `google.auth.transport.requests.AuthorizedSession`. Interactive gcloud user
  credentials sometimes need reauthentication; short-lived ADC token files were used
  with `--access-token-file` and removed afterward.
- Cloud Logging requests should include `x-goog-user-project: teak-banner-dome` and
  a narrow timestamp filter. Missing quota-project headers caused errors; broad scans
  were slow. Application JSON output may appear in `jsonPayload`, not `textPayload`.
- For IAP API checks, the authorized service account is
  `face-batch-merge-cli@teak-banner-dome.iam.gserviceaccount.com`. Existing private
  scripts use IAM `signJwt` with account as issuer/subject and console origin + `/*`
  as audience, then Bearer auth. Mutation requests also need `Origin` equal to the
  configured console origin; omitting it yields 403 `origin_rejected`.
- Cloud SQL uses a private address. Temporary Cloud Run jobs cloned from a known
  console/backfill template were used for tightly scoped DB diagnostics/repairs;
  startup often took 1–4 minutes. Use read-only transactions for diagnosis, explicit
  row/state/URL guards for authorized repairs, save evidence and delete temporary jobs.
  pg8000 returns rows as lists: convert to tuple before comparing against a tuple.
- Storage objects can use CSEK. Use existing `worker.storage` helpers and secret
  `face-batch-gcs-csek`; never log keys. Preserve generation preconditions for writes.
- Private `.local/` contains useful diagnostics, source HTML, media and scripts.
  It is not a public artifact directory. Deployment token files/custom Docker logins
  created for the latest work were removed/logged out. Do not assume older private
  scripts or snapshots reflect current live state.

## Release notes to consult

Under `data-batch-gcp-scaffold/docs/history/`:

- `2026-09-15-automatic-source-matching.md`
- `2026-09-15-profile-ui.md`
- `2026-09-16-disable-example-ui.md`
- `2026-09-16-historical-source-matching.md`
- `2026-09-16-neutral-ui-and-batch-policy.md`
- `2026-09-17-acquisition-verification.md`
- `2026-09-17-longer-historical-matching.md`
- `2026-09-17-luluvdo-support.md`
- `2026-09-17-automatic-enrollment-selection.md`

Also consult `OPERATIONS.md`, `docs/source-merges.md`, and `openapi/phase1.yaml`.
Earlier release notes intentionally retain the threshold/settings that were true at
that release; use the latest note and live configuration for current behavior.

## Subsequent local script update — 2026-09-18

`bulk-download/scripts/export-search-links.js` now also handles the `/users`
directory, collecting `/user/...` URLs and following `/users?page=N` links from
page one. It downloads `user-links.txt` (or `user-links-partial.txt`), deduplicates
profiles, and preserves existing search/video export behavior and the
`window.videoLinkExport` controls. See the new README browser-export instructions
and `bulk-download/tests/browser/test_search_export.py`. This is a local browser
console script change; no cloud deployment or bulk submission is involved.

## User-list cloud backfill launched — 2026-09-18

The user supplied root `user-links.txt`; it was copied unchanged to
`bulk-download/input/hotscope_users.txt` (935 unique valid profiles). They explicitly
requested skipping further tests and launching after the deduplication explanation.

Active job `face-user-backfill-20260918`, execution
`face-user-backfill-20260918-kbxd6`, backfill ID
`ffdd46ef-ffe6-4550-ab88-a7923e858251`. Runs 935 tasks at parallelism 1 with
retain_and_enroll, three retries per task, and the existing three-active-run quota.
Each task runs the existing bulk acquisition worker for one user with a standard
profile batch checkpoint. A task finishing means handoff complete, not necessarily
all videos acquired successfully or all GPU runs completed. Check the profile
checkpoint status and run outcomes as well as Cloud Run task counts.

New module: `data-batch-gcp-scaffold/worker/user_backfill.py`. Dedicated image:
`acquisition-batch@sha256:8557fed9d0dd8ab94fe1a17e24b7c8c1731d4b4575c73f0bcd5b21ff2d7cee90`.
This is a separate operational job; normal acquisition job/image was not replaced.
Input generation and checksum are pinned. Same-execution retries resume safely;
a new execution is rejected if a profile checkpoint belongs to another execution.
Do not blindly rerun the job to resume it. See
`docs/history/2026-09-18-user-list-backfill.md` under the scaffold and private
`.local/user-backfill/launch.json` for the task/batch mapping and exact input location.

Deduplication confirmation was scoped: stable video IDs/archive paths and
library-wide SHA-256 enrollment reuse prevent new subjects for exact enrolled
content, including concurrent runs. Different encodings may hash differently.
Different provider IDs can still produce extra acquisition archive objects; the
library-wide enrollment guard does not mean global archive-object deduplication.

### Backfill status snapshot — 2026-09-19 ~00:07 UTC

20/935 profile tasks handed off, task index 20 active; 203 video acquisitions,
zero acquisition failures, 181 distinct submitted/preexisting run IDs, 21 acquired
objects awaiting submission in the active profile. Job remains parallelism 1.
The old Luluvid runs 254e4e60-1eec-432d-a5c3-1259bb58f184 and
052d9300-dd8b-419e-9244-264179d89514 are both awaiting_face_selection and consume
two of the shared three active-run slots. This leaves only one slot for the backfill.
No selection or concurrency changes were made during the status inquiry.

Crude constant-profile-throughput extrapolation: ~34 additional days (around Oct 23).
Execution elapsed-time-based Cloud Run compute estimate ~$12–13 so far, ~$580 total
if remaining users resemble the first 20; not actual billing or a reliable budget.
Excludes storage, Cloud SQL, networking and discounts; elapsed execution time can
include provisioning. Cost and ETA must be re-estimated after clearing the slot
bottleneck or as larger profiles are discovered. Snapshot evidence is in private
`.local/user-backfill/progress-current.json`, `current-profile-runs.json`, and
`cost-executions.json`.

At ~00:11 UTC on 2026-09-19, the user actioned both paused Luluvid runs.
Verified: 254e4e60-1eec-432d-a5c3-1259bb58f184 succeeded;
052d9300-dd8b-419e-9244-264179d89514 was matching (not yet terminal).
The active backfill profile had advanced to 31 handed-off/reused objects,
19 awaiting submission, with one queued and one fetching. One blocked slot was
already freed; the other will free when the matching run becomes terminal.

## Navigation and direct-merge UI release — 2026-09-19

Latest console image is now
`console@sha256:ee03ec65be9e2957d8dea58243debb45cfbc76fb077b7b1fa9ae8bbdfa76b168`,
revision `face-console-00051-7bq`, 100% traffic. Ingestion remains on its prior
image; this release changes UI assets only.

Results have half-width View Source / Open Source at the top and three equal
View Subject / View Source / Open Source columns on result/subject cards.
External source links have an icon. Source-list checkbox labels are source IDs;
subject merge checkbox labels are display names. Requested helper/self-link text
was removed and matching/selection actions fill their rows.

Potential-match header actions are all on one equal-width row. Each card has
full-width Merge and Dismiss. Card Merge bypasses the modal and combines into the
currently viewed subject using existing version checks and stable retry IDs.
Bulk/manual merge flows still have their prior review flow. See
`docs/history/2026-09-19-navigation-and-direct-merge-ui.md` under the scaffold and
private `.local/ui-navigation/` evidence. Focused real-DB browser merge check passed;
mobile/desktop navigation and intercepted retry checks were also performed.


## Run-button wrapping correction — 2026-09-19

Removed percentage-height stretching from navigation buttons. Run result navigation
now places individual View Subject / View Source / Open Source buttons into a
responsive grid (up to three columns) rather than stretching nested source columns.
Buttons remain single-line, 44px high; additional sources wrap to subsequent rows
and narrow cards reduce the column count. Checked overflow and button bounds at
320, 390, 768 and 1280px with a 12-source result.

Latest console image: `us-central1-docker.pkg.dev/teak-banner-dome/face-batch-worker/console@sha256:e6eac8aa82a515823d2a9d7a3f2954345aafadcb8e4e94d39b47a77211b20d56`.
Only app.js/styles.css changed in this correction; processing jobs are unchanged.
Private deployment and browser evidence: `.local/ui-wrap/`.

## 2026-09-19 run/source navigation
Console revision face-console-00053-crt, image console@sha256:15c984d58d56ac24046a0ade1a7098b7bbcd80583cb84aea4f3188881e69c0ad. Recent runs and batch entries have View Source buttons. Run serialization adds source/source_url using retained source or matching SHA, preserving idempotency fingerprint as existing[-1]. Profile batches forward source_url. When a run has attribution but no library asset (including no_faces run 1f35b6fc-89c6-4d85-8f6b-fd36d3859208), View Source opens /runs/<id>/source: a source overview with the external original link and View Run. This does not create a library record or change retention. Existing library sources still use /sources/<id>. Subject examples show View Run/Open Source in a two-column row. Unit checks 36 passed, isolated DB checks 9 passed, focused browser checks 2 passed. Private deployment/verification artifacts ../.local/source-navigation. Console only; processing jobs unchanged.

## 2026-09-19 source library correction and subject button sizing
Supersedes the previous run-only source overview behavior. User explicitly wants
zero-candidate/zero-subject media in the source library. New worker/source_registry.py
registers source metadata at successful detection-with-no-groups and successful
matching, using enrollment's global content lock and exact SHA reuse. No subjects
are created by registration. Source browsing now starts from nondeleted source_asset
with left joins, including zero-subject sources. View Source is /sources/<id> only.
Old /runs/<id>/source bookmarks redirect when the run has its library source.
Source metadata registration does not recover previously deleted temporary bytes.

GPU image worker@sha256:69d10e6f6e984b4badac23a5821ebea9e65d5976596cb8850e4036134dc557a2.
Console current revision face-console-00055-s2j, image
console@sha256:c3935a869b3bdf94571d72a09cd3e56ddede4f3196ac1642f5db3526c627f01e.
This includes subject top source buttons in equal halves and 36px example buttons.
36 unit and 9 targeted isolated DB/browser checks passed; live subject sizing checked
at 390/1280px. Private artifacts ../.local/empty-sources and ../.local/subject-button-sizing.
Historical backfill job face-empty-source-backfill-88006ecf, execution
face-empty-source-backfill-88006ecf-qcnjm was launched; see subsequent completion note.

Source registration backfill completed successfully at 2026-09-19 01:30:58 UTC:
169 completed runs registered/linked. Temporary backfill job deleted afterward.
Live verification: run 1f35b6fc-89c6-4d85-8f6b-fd36d3859208 now links to library
source 8757d033-31bc-4e5c-885f-6d84cd3f0236 with 0 subjects/0 examples and its
original external URL. Both View Source and the old overview bookmark reach
/sources/8757d033-31bc-4e5c-885f-6d84cd3f0236. No pending work in this change.

## 2026-09-19 candidate button rows
CSS-only console release console@sha256:a8029b79cf0f003a11018362129026b080c3b4ae759ec98ccde7292e19bb29f3.
Candidate View Subject spans the full row; View Source and Open Source each occupy
half of subsequent rows. Also applied to shared subject cards with View Subject.
Existing open-subject two-column navigation and compact example actions preserved.
Browser layout checked at 390/768/1280px with multiple sources. Private artifacts
../.local/candidate-buttons. Processing images unchanged.

## 2026-09-19 compact source counts
View Source count labels now read `View Source (3)` (no `subjects`) on subject
cards and source-library cards. Console image console@sha256:72657376882ca22bce4bc48d6aede3a706eb83cacc78c21a97abc97c1cac8beb.
Private release/verification artifacts ../.local/source-count-label.

## 2026-09-19 source library filters and card sizing
Console image console@sha256:92a436a8b71590b8e039db5aac750054b9b1ee6aa0bd3db9179cb4cbac978bb2.
/sources cards have smaller single-line IDs and half-width, 36px source buttons.
Subjects per source dropdown: all/none/single/multiple; API subject_scope parameter
uses current subject counts and preserves legacy multiple_subjects=true. Existing
ID/URL search, website and sort controls remain; filters persist in URLs/pagination.
24 console unit tests and five targeted real-DB tests passed; layout checked down
to 320px. Artifacts ../.local/source-library-sizing and release note
docs/history/2026-09-19-source-library-filters.md. Processing jobs unchanged.

### Live source-filter verification and console latency
Live API checks returned 200 and correct subject counts for none (0), single (1),
and multiple (8). Assets match deployed files; live card layout passed. Full
browser reload/filter script was interrupted by 30-second request timeouts, so do
not claim that whole script passed. Filters otherwise passed isolated DB tests.
User asked why requests are slow. Read-only diagnosis found console runtime:
1 CPU/512Mi per instance, max 3 instances, Cloud Run concurrency 20, 30s timeout,
gunicorn 2 synchronous workers per instance. Gallery requests synchronously query
DB then fetch private JPEG from storage. Recent sample: 41 gallery requests among
67 HTTP entries, gallery latency up to 28.29s, /api/sources 504 at 30s. This strongly
suggests queueing/shared worker contention; exact DB-vs-storage time not measured.
No resource/concurrency changes authorized or performed in this diagnosis.
Private evidence ../.local/source-library-sizing/latency.log and check-latency.py.

## 2026-09-19 lazy gallery images
Console revision face-console-00059-vp6, image us-central1-docker.pkg.dev/teak-banner-dome/face-batch-worker/console@sha256:ea61a8373196832a9cd37d885773ba4a916c9148a60aa68967f90a34dbb4fe29.
UI-only: all image construction paths use setGalleryImage; IntersectionObserver
loads images within 200px of viewport, with loading=lazy/decoding=async. Removed
pending images are unobserved. Deterministic browser check: all 50 candidates and
scores preserved, under ten image requests initially, scroll loads last image.
No matching backend/threshold/input/budget changes. User explicitly emphasized
matching efficacy; display loading is independent. Source scan budget remains
10 seconds; lazy loading is not a fix for large scans. Private artifacts
../.local/lazy-images, docs/history/2026-09-19-lazy-gallery-images.md.

## 2026-09-19 authorized 0.3 matching on sources over 70 subjects
User explicitly authorized applying 0.3 group matching to every source with more
than 70 current subjects. Inventory selected 13 sources, 1,130 subjects, max142;
includes f0b42daa-91be-51a6-a61b-d2eaa4de3185. Existing prior/manual matching does
not disqualify these sources for this newly authorized pass. Default upload0.4
and UI scan limits unchanged. Existing matcher exclusions/identity guards preserved.
Job face-point-three-matching-46062e60, backfillid76f00a2d-691a-4d68-84d6-ccf2ab8654cc.
Encrypted report in bucket teak-banner-dome-bulk-videos at
batch-control/hotscope-20260914/high-subject-matching/76f00a2d-691a-4d68-84d6-ccf2ab8654cc.json.
Private operational runner/launcher/status at ../.local/point-three-matching/.
Uses current console image (matching code) and cloned source-backfill job config,
1 task, 300sec planning budget, per-source transaction under shared content lock,
metadata high_subject_matching for atomic replay and GCS checkpoint after each.
Checks example counts unchanged. Sources rechecked >70 before apply. At launch,
queued/running; DO NOT claim completed until report/outcomes verified. See followup.
0.3 job first attempt failed every source before mutation: current console image's
SourceMerges.auto_merge lacks actor/scan_budget_seconds. Built dedicated image
from that console plus current worker/source_merges.py in both /app and site-packages.
See point-three-matching/image.txt. Retried same job/checkpoint after updating
its template, preserving operation scope and source inventory. Cloud Run Jobs v2
PATCH does not support updateMask (omit it). Report may contain prior TypeError
outcomes/summary until the corrected execution replaces them; count non-error
outcomes for actual progress. No console/GPU defaults changed for this correction.

### User changed unfinished sources to 0.4
User clarified "remaining" means unfinished in the current 13-source pass, NOT
all other sources. First committed0.3 source f0b42daa-91be-51a6-a61b-d2eaa4de3185
went142->16, all147examples preserved. Requested cancellation of active execution,
then waited for every task's Completed condition to become terminal before changing
checkpoint threshold to0.4. (Execution completionTime alone is insufficient after
cancel: original task continued writing briefly, causing a412 checkpoint collision;
DB metadata made recovery idempotent.) switch-four.py enforces task-terminal checks.
Continuation uses same scope/job/report/backfillid and runner-four.py; threshold0.4
for12unfinished sources, completed0.3 outcome retained. maxRetries now0. No other
sources added and no automatic upload threshold/config changes. Four-operation.json
stores launch operation. Monitor encrypted report via status.py; reportmetadata
high_subject_matching supports recovery if a checkpoint fails after DB commit.
A read-only diagnostic job face-point-three-diagnostic also exists and can be
removed once no longer needed. Snapshot showed source browsing SQL itself active
for20sec with no blockers, so latency is not solely image queuing.

## 2026-09-19 queued follow-on 0.4 pass for sources over 40
User authorized: after current 13-source pass completes, apply same0.4 group merge
to any sources with more than40 subjects. Durable Cloud Run job launched:
face-over-forty-matching-ccbaf08a, backfillid436fe083-857f-4c43-80b8-59974c38d710.
Private scripts/scope/operation: ../.local/over-forty-matching/.
Own report (created only AFTER parent finishes):
batch-control/hotscope-20260914/high-subject-matching/436fe083-857f-4c43-80b8-59974c38d710.json
in teak-banner-dome-bulk-videos, encrypted with configured CSEK.
Gate polls prior76f00a2d report every30s until its finalsummary and all outcomes
exist with no errors. It refuses to proceed after a failed/incomplete parent.
Then takes fresh DB inventory of nondeleted sources with >40 distinct current,
unmerged subjects. One pass, 0.4, 300sec planning/source, existing exclusion/identity
checks and example-count invariants. Rechecks >40 before each source. Uses a new
backfillid and same high_subject_matching metadata replay protection; prior immutable
GCS report/audit records remain. maxRetries1,24hrtasktimeout,waitbudget20hr.
No changes to ordinary upload policy/threshold or service resources.
Latest task here is scheduling this follow-on; it is queued, NOT yet completed.
Read-only diagnostic job face-point-three-diagnostic was deleted.

## Status check: first pass complete; follow-on recovered
First 13-source pass completed at2026-09-19 03:03:04UTC:1,130->473subjects,
256groups merged, all1,858examples preserved; no source remains above70.
Follow-on face-over-forty-matching-ccbaf08a failed in its wait gate BEFORE inventory
or merges: GCS Blob.download_as_bytes populates generation on the handle, so
reusing the Blob fetched a replaced/deleted old report generation (404). Gate now
creates a fresh Blob handle every poll. Regression check simulated checkpoint
replacement and passed. Restarted same job/backfill/report, new operation in
../.local/over-forty-matching/restart-operation.json. At this status update it is
queued/starting; no completion claim for over40 pass. status-both.py reads both
execution states and encrypted reports; status-both.jsonl is last snapshot.

## Over-40 pass completed successfully
Verified execution face-over-forty-matching-ccbaf08a-bxbpx succeeded at
2026-09-19 07:17:59UTC after3h31m52s. All88selected sources completed:
4,441->2,206subjects,1,095groups merged,all8,242examples preserved,no source errors.
Three sources remain over40 after this0.4 pass:4d698952-dc99-4cff-b412-7b396bc1a4db,
047d8450-7d73-4c5b-ac1f-f890615b962d,bc6641e0-c8f0-5547-b1f8-cc4ff926535d.
Full local report ../.local/over-forty-matching/latest-report.json; encrypted
GCS report remains at the recorded scope path. Both authorized passes complete;
no further threshold changes or repeat passes authorized by status requests.

## 20-second interactive source scan
User explicitly requested only expand interactive window to20seconds.
SourceMerges.plan deadline changed10->20; console revision face-console-00060-k5q,
image us-central1-docker.pkg.dev/teak-banner-dome/face-batch-worker/console@sha256:db4f0964fbb89afa27bb46ebe541802d2916875995412e61c21da0f6edd905bc. Console30s request timeout unchanged.
Automatic matching default10s and background budgets unchanged.12focused source
matcher/CLI unit checks passed. Private deployment/probe artifacts ../.local/scan-window.
Preview investigation for046c24da-e49d-4155-a32f-135805024975: all32current subjects
return0representative images; sampled example has face_track_id/processing_job_id,
no submission_group/run_id and no preview_url. It is historical track data, not
a lazy-loading suppression. Read-only DB diagnostic launched for gallery metadata.

Preview diagnosis complete for046c24da-e49d-4155-a32f-135805024975:
104examples, all historical face_track records; 0gallery representative records
(total/active/retired all0). Archived source object reference still present; source
created2026-09-03. gallery_fallback_source recorded no_unambiguous_crop at
2026-09-03 22:37:47UTC: prior thumbnail regeneration could not confidently
associate regenerated crops with stored tracks. This predates merges/lazy loading.
No thumbnail regeneration performed in this request (user asked why). Read-only
diagnostic job deleted after successful result.
Live20sec source scan probe returned200 in6.7sec and7groups at0.4; no merges applied.

## Scan error wording and platform latency investigation (2026-09-19)
Interactive 20-second budget is active; recent scan requests include both successes
and budget failures. Corrected misleading human-facing background-scan instruction
in worker/source_merges.py: timeout now says scan exceeded its time limit, no merge
proposal created, please try again. Limit error likewise no longer suggests an
unavailable UI workflow. Machine error code preserved. 12 focused matcher/CLI tests
passed. Deployed face-console-00061-p6j, image sha256:49fbe6960fa8f7f14c072613526fc6361679c05fb5f6d15eecfb447278c18cd9;
ignored tfvars image/rollback updated. No budget, resource, or background-job changes.
Read-only diagnostic for046c24da-e49d-4155-a32f-135805024975 found32members,
7groups at0.4: connection3.69sec, database load40.25sec, matching0.036sec.
Main subject/example aggregate query in SourceMerges._load took36.724sec;
summary2.018sec, representatives0.741sec, source URLs0.504sec, dismissals0.23sec.
This establishes SQL latency dominates this sample, not cosine calculation.
SQL instance face-batch-pg remains db-f1-micro (0.6GiB RAM); Monitoring previous
30min memory utilization1.0 throughout, CPU mean0.217/max0.241. Memory metric alone
is not proof of cause; no contention A/B test performed. Recent sources/subjects
APIs ~10sec and gallery ~9sec. Console1CPU/512MiB,2 synchronous gunicorn workers,
concurrency20/max3instances; slow synchronous work can queue other requests.
Both matching backfills complete; user import face-user-backfill-20260918-kbxd6
and ingest/GPU work still active, sharing DB. Can contribute but not proven sole cause.
Private diagnostics/deploy artifacts ../.local/platform-latency. Temporary profiling
job face-scan-profile-aa523f52 succeeded; deletion accepted. No query optimization,
resource scaling, or pause of active backfill performed. User previously rejected
larger background-scan implementation in favor of only raising window to20sec.

## 2026-09-19 worker video duration limit

Run `ee36c313-7ad5-4ece-8d99-73e8f7b75a8c` failed detection with `invalid_media`: its checksum-verified source is 1,683.128 seconds long, exceeding the previous 900-second limit. User requested expanding the worker limit to 30 minutes. Updated both defaults in `worker/config.py` and the interactive GPU job environment in `terraform/console.tf` to 1,800 seconds. Patched the live `face-interactive-gpu` job and read back generation 30 with `FACE_MAX_PROBE_VIDEO_DURATION_SECONDS=1800`; applies to new executions. Configuration checks and Terraform formatting/validation passed. The failed run was not resubmitted. Deployment operation: `.local/performance/gpu-duration-30m-operation.json`.

## 2026-09-19 performance implementation in progress

Active objective: implement `data-batch-gcp-scaffold/docs/performance-implementation-plan.md`. See `docs/history/2026-09-19-performance-implementation.md` for release evidence and remaining verification. Preserve all uncommitted changes, including new queue modules, migration 027, tests and acquisition Dockerfile.

Live: schema 27; Cloud SQL `db-g1-small`; console revision `face-console-00065-8sz`; two batch slots plus one interactive slot; two-profile acquisition continuation `face-user-backfill-perf2-20260919-pvk9n`. Forty original profiles had completed acquisition/handoff when this continuation selected the remaining 895. Original backfill/checkpoints preserved. Current import is ongoing, not complete.

A pilot found GPU enrollment requires two pooled DB connections for independently committed cleanup receipts. GPU template now sets `FACE_DB_POOL_SIZE=2`; targeted success/rollback regression passed. Runs `f46991e6-6c7f-44fe-9be9-9198e8837fb8` and `2dbde631-b617-4699-8ece-cd5ed2025bb5` were requeued for matching after all old execution tasks became terminal. Verify their outcomes and subsequent normal dispatch before declaring success.

Private operational scripts/evidence: `.local/performance/`. `pilot-status.py` observes the final continuation and queue; `processing-sample.py` reads new processing runs; `pilot-metrics.py` reads actual billable instance time; `pilot-logs.py` reads stage logs. Current remaining work: final runtime verification, sustained throughput/cost comparison and revised completion estimate, final scope audit, temporary diagnostic-job cleanup. Do not restart an execution based on an observation timeout.

Follow-up verification: both named matching operations succeeded at approximately09:22UTC. All49 and2 selected groups respectively retained their examples and representatives; later ordinary processing also succeeded. Second100-request mixed check: source/subject/gallery p95 0.203/0.249/0.294sec, all200. Final acquisition image smoke check and Terraform validation passed. Temporary performance diagnostic jobs deleted. Remaining goal work is runtime sampling/cost and ETA analysis, dispatcher rollover, and final audit; see private pilot scripts and release note.

Performance implementation completed and pilot-verified: normal processing succeeded after the two-connection GPU pool correction; completed-run checks preserved all selected examples and representative records. Dispatcher `face-ingest-drain-mcrk5` exited successfully09:32:04UTC and a replacement acquired its lease around09:32:33UTC. Ten-minute pilot09:22–09:32:8successful videos,0failures=48/hour versus17in30minutes=34/hour earlier. Acquisition149records/10minutes while queue had room. Estimated import compute$27–35/1000videos with minimum-billing allowance; database$27.25/month including10GiBSSD. Snapshot44discoveredprofiles/728videos,43handoffscomplete,891undiscovered,226knownpending. Observed-mean remainder scenario~14,969videos/~13days at48/hour/$403–527importcompute; assumptions and wider scenarios documented in the release note. Import continues in `face-user-backfill-perf2-20260919-pvk9n`; do not restart it. Higher concurrency/model reuse remain optional later experiments. Final plan/release evidence are reviewable; changes remain uncommitted.
