# Small-team evolution plan

## Purpose

The current system proves the core face-processing workflow for one trusted developer:

1. acquire videos locally and archive them in CSEK-encrypted GCS;
2. process selected videos with Cloud Run GPU Jobs and build a Cloud SQL/pgvector
   subject gallery; and
3. submit local images or videos to a read-only probe that returns ranked candidate
   subjects.

The next goal is a small-team system that usually accepts a link, also permits direct
picture or video upload, returns candidate subjects, and asks whether the submitted
media should be retained. Retention always means enrollment: retained media becomes
recoverable training material and the face groups selected by the user are added to the
gallery. A later goal is authorized review of the encrypted source evidence behind a
candidate.

This document evaluates that direction. `ARCHITECTURE.md` remains the description of
what exists today.

### Technical implementation

Build the team workflow alongside the current CLI rather than replacing working paths
immediately. Reuse `worker.detector`, `worker.tracker`, `worker.quality`,
`worker.embedder`, `worker.aggregate`, and the pgvector query code. Add new web,
submission, and gallery modules behind separate entry points so local ingestion and
`face-probe` remain usable during rollout.

## Terminology

The system does not train a new neural network for each person. SCRFD and AdaFace remain
fixed inference models. “Create a model” in this plan means detecting faces, creating
AdaFace embeddings, aggregating observations, and adding or updating an anonymous
subject representation in the gallery.

Keeping this distinction explicit matters for versioning, deletion, user expectations,
and determining whether retained media must be reprocessed after a model upgrade.

### Technical implementation

Use `subject`, `face_track`, and `canonical_embedding` consistently in schemas and API
payloads. UI copy may say “face model” for accessibility, but API documentation should
define it as a versioned subject embedding aggregate. Persist detector, embedding,
aggregation, and threshold versions on every run and enrollment result.

## Current-state fit and gaps

| Capability | Current state | Change needed for a small team |
| --- | --- | --- |
| Video acquisition | Local JustPaste/Luluvid workflow and append-only manifest | Add authenticated link-first ingestion with direct upload as a fallback |
| Image ingestion | Images are accepted only by the local probe | Add image enrollment and user selection of detected faces |
| Managed processing | Durable Cloud SQL queue and Cloud Run L4 worker | Generalize work items to image and video submissions |
| Candidate search | Local, read-only, ranking-only probe | Expose an authenticated team-facing asynchronous submission API/UI |
| Media retention | Training videos are durable; probe media is local-only | Make retain-and-enroll versus discard-after-search an immutable submission policy |
| Gallery growth | Training videos update subjects; probes cannot | Enroll selected face groups from every retained submission through the managed worker |
| Result access | Printed locally with local review files | Provide bounded result retrieval suitable for asynchronous jobs |
| Visual subject gallery | No centrally retained representative crops | Show representative enrolled faces on initial candidate cards and subject pages |
| Candidate evidence | Provenance and timestamps are returned; a developer can manually decrypt source media | Add an authorized service that retrieves or streams evidence without revealing the CSEK |
| Authorization | Developer ADC and broad local capabilities | Add authentication with one approved internal-user role |
| Concurrency | One active rollout and parallelism one | Separate interactive work from bulk rollouts and make subject updates concurrency-safe |

The existing detector, tracker, quality scoring, embedding, pgvector ranking, Cloud SQL
queue, source provenance, and Cloud Run worker are strong foundations. The principal
gaps are policy, authorization, image enrollment, result delivery, concurrency, and
secure evidence access rather than face-model inference.

### Proposed component layout

```text
Browser UI
  → face-console Cloud Run service (CPU, authenticated)
      → Cloud SQL submission/status/result rows
      → GCS resumable-upload session creation
      → shared CPU ingestion drain for submitted links
      → face-interactive-gpu Cloud Run Job with detect, match, and backfill modes
      → CSEK gallery/evidence streaming endpoints

Existing bulk tools
  → existing manifest and face-batch-gpu-drain path
  → same processing and gallery modules
```

Use one deployable web service and one new backend identity shared by `face-console` and
the CPU ingestion drain. Continue using the existing GPU-worker identity for managed
inference. Add a separate evidence identity only when Phase 3 is implemented. Split
services further only when security boundaries, timeout behavior, or measured load
justify it.

## Submission contract

The UI should offer one clear control: **Retain and enroll this media**. The durable API
value should be an explicit policy rather than a nullable boolean:

```text
handling_policy: search_then_discard | retain_and_enroll
source: {kind: url, url: ...} | {kind: upload, upload_id: ...}
```

- `search_then_discard` returns candidate subjects, does not change the gallery, and
  deletes the exact temporary media generation after terminal processing.
- `retain_and_enroll` returns the same pre-enrollment candidates, promotes the media to
  durable CSEK storage, and enrolls the user-selected face groups into the gallery.

There is intentionally no retained-but-not-enrolled mode. The policy, authenticated
principal, purpose, timestamps, source URL where applicable, model versions, candidate
results, selected face-group IDs, and enrollment outcomes must be recorded. The
candidate response must preserve what the gallery contained before enrollment so the
new submission cannot appear to have been its own prior match.

Every approved internal user may select `retain_and_enroll`. Authentication and the
closed user allowlist are the initial boundary protecting gallery writes.

Phase 1 exposes only `search_then_discard`. The UI hides the retention control and the
API rejects `retain_and_enroll` with a stable `feature_not_available` error until the
Phase 2 enrollment gates pass. It must not accept a retention request and defer it.

### Technical implementation

Create `POST /api/runs` with an idempotency key and one source object:

```json
{"handling_policy":"search_then_discard","source":{"kind":"url","url":"https://example/media"}}
```

```json
{"handling_policy":"retain_and_enroll","source":{"kind":"upload","content_type":"video/mp4","bytes":123456}}
```

Generate `run_id` server-side as UUIDv4. Store the handling policy immutably; changing
it requires cancelling before processing and creating a new run. Return HTTP `202` with
the run ID, state, and status URL. Reusing an idempotency key for the same internal user
returns the original run and rejects a different payload.

## Link-first input

The primary UI should accept an HTTPS link. A backend ingestion service resolves and
fetches it under strict SSRF, redirect, DNS/IP, content-type, byte, duration, and timeout
controls, then writes the content to temporary CSEK-encrypted GCS. Never fetch arbitrary
links in the browser or in the GPU worker.

Support these source adapters behind one interface:

1. direct HTTPS image or video URL from a public host;
2. direct browser upload for files without a usable link; and
3. direct JustPaste and Luluvid URLs through the existing source-specific resolver.

The existing JustPaste-to-Luluvid resolver should remain as an isolated adapter while
it is still useful, but it should not define the public submission contract. Measure its
ongoing use and maintenance burden before promising permanent dedicated support. New
source adapters must produce the same normalized immutable media record; downstream
processing must not care whether input came from JustPaste, Luluvid, another link, or a
file upload. Do not accept HeyLink in the UI because its interactive challenge does not
fit server-side resolution. Reject arbitrary HTML pages that are not handled by an
enabled source adapter.

### Technical implementation

Normalize every resolver result into `{final_url, source_adapter, content_type,
expected_bytes?}`. Extract the bounded reads and media checks from `bulk-download`, but
do not reuse its current host check as the SSRF boundary: it validates one DNS lookup
and then allows the HTTP client to resolve the host again when connecting. The shared
fetch component must resolve once, reject every non-public address (including
IPv4-mapped IPv6), connect only to one of those validated addresses while preserving
the original TLS SNI and HTTP Host, and repeat the process independently for every
redirect and retry. If that transport cannot be proven before Phase 1, limit link
fetching to explicitly allowlisted adapters and direct upload; do not enable arbitrary
public hosts.

Run link retrieval asynchronously with `run_id` through one shared CPU Cloud Run Job
named `face-ingest-drain`, not in the request-serving service. `face-console` inserts a
work item and invokes the existing job definition through the Cloud Run Jobs API. A
single-task execution drains available work using database leases and exits when the
queue is empty. Concurrent invocations are harmless because workers claim rows with
`FOR UPDATE SKIP LOCKED`; initially cap job parallelism and CPU ingestion concurrency at
one. A scheduled reconciliation execution picks up work left queued after a failed
invocation. Write only to
`submissions-temporary/<run_id>/source.<ext>`, use `if_generation_match=0`, calculate
SHA-256 while streaming, and record the resulting generation and size. Direct media may
come from any public host that passes all network and media validation. Enable
JustPaste/Luluvid as a deployment-configured adapter so a failure there cannot affect
direct links or uploads.

Reject embedded credentials, non-HTTPS URLs, private/reserved/metadata IPs, excessive
redirects, unsupported content, encrypted HLS, oversized media, and authentication
challenges. Store a canonical source-page URL, but never store signed media URLs or
response headers.

## Proposed end-to-end flow

```text
Authenticated team member
  → submit a link or request a direct-upload session
  → create a run with handling_policy
  → backend places media in private temporary CSEK GCS
  → enqueue durable processing work
  → Cloud Run GPU worker detects/tracks faces and creates review previews
  → user selects the face groups to use
  → embed and rank selected groups against the pre-enrollment gallery
  → show candidate cards with representative gallery faces
  → enroll if and only if handling_policy=retain_and_enroll
  → retain the encrypted source or delete the exact temporary generation
  → user opens the run-status UI to view progress and results
```

The API should immediately return an opaque UUID `run_id` and a status URL such as
`/runs/{run_id}`. The UI should show the ID with a copy button and provide a separate
**Check a run** entry point where an authenticated user can paste it. A run ID is a
locator, not a credential: every status/result request must still authorize the caller.
With the initial single role, all approved `internal_user` members may view and resume
all runs. Record the submitter for context, but do not add a per-run sharing model yet.

Run states are `awaiting_media`, `fetching`, `queued`, `detecting`,
`awaiting_face_selection`, `matching`, `enrolling`, `succeeded`, `failed`, `cancelled`,
and `expired`. Zero detected faces completes as `succeeded` with an empty result and a
`no_faces` outcome. Zero selected faces remains `awaiting_face_selection`; the user may
revise the selection or cancel. Selection becomes immutable when matching starts.
Cleanup and promotion use operation states rather than multiplying run states, and a
failure records its owning step and whether it is retryable. The status page should show
safe progress, timestamps, retry state, and
sanitized errors. When detection completes, that same page should present face groups
for selection. It may poll with backoff; email or another notification can be added
later. Image processing may be quick, but link retrieval, video processing, user
selection, and GPU startup make a long synchronous HTTP request unreliable.

Allowed principal transitions are intentionally small:

```text
awaiting_media → queued | expired | cancelled
fetching → queued | failed | cancelled
queued → detecting | failed | cancelled
detecting → awaiting_face_selection | succeeded(no_faces) | failed | cancelled
awaiting_face_selection → matching | expired | cancelled
matching → succeeded | enrolling | failed
enrolling → succeeded | failed
failed(retryable) → owning pre-failure state
```

Cancellation after a worker starts is cooperative: record `cancel_requested`, stop at a
safe checkpoint, then enter `cancelled` and enqueue cleanup. Cancellation is rejected
once enrollment begins. A selection ETag permits revision only while
`awaiting_face_selection`. Cleanup failure leaves the terminal run unchanged and its
operation retryable; promotion failure occurs before enrollment and fails that step.

Because the operation is asynchronous, some durable request and result state is
unavoidable even when media retention is disabled. That state should contain only
sanitized metadata, status, policy decisions, candidate identifiers/scores, and
diagnostic information, with a defined short retention period. The uploaded media, raw
crops, and probe embeddings can still be deleted immediately after terminal processing
when the submission says `search_then_discard`.

### Technical implementation

Persist transitions with compare-and-set updates such as `UPDATE ... WHERE run_id = ?
AND state = ?`. Workers lease runnable steps with `FOR UPDATE SKIP LOCKED` and renewable
lease expirations, following the current rollout queue. Every step must be idempotent so
a retry cannot duplicate promotion, candidate rows, enrollment, or deletion.

Suggested endpoints:

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `POST` | `/api/runs` | Create a URL or upload run |
| `POST` | `/api/runs/{run_id}/upload-session` | Return a scoped resumable session URI |
| `POST` | `/api/runs/{run_id}/upload-complete` | Finalize upload with expected size and SHA-256 |
| `GET` | `/api/runs/{run_id}` | Return state, progress, and sanitized error |
| `GET` | `/api/runs/{run_id}/face-groups` | Return temporary selection previews |
| `PUT` | `/api/runs/{run_id}/face-selection` | Confirm selected group IDs |
| `GET` | `/api/runs/{run_id}/results` | Return candidates and enrollment outcome |
| `POST` | `/api/runs/{run_id}/retry` | Retry an explicitly retryable failed step |
| `POST` | `/api/runs/{run_id}/cancel` | Cancel a run that has not begun enrollment |

Return `404` for unknown run IDs. Any approved group member is authorized under the
initial team-wide visibility rule. Use a row version or ETag for selection updates so
two browser tabs cannot overwrite each other. A retry is accepted only for the step
recorded as failed; the worker that owns that step owns the retry. Enrollment of all
selected groups is one idempotent database transaction, so the run cannot expose a
partially enrolled result. Complete media promotion before that transaction; if
promotion fails, remain retryable without gallery mutation.

## UI and serverless shape

Use a lightweight web UI backed by a small authenticated Cloud Run service. That
service owns submission creation, link validation, resumable-upload initiation, run
status, and result delivery. Keep GPU inference in the existing Cloud Run Job rather
than holding an HTTP request open. Cloud SQL remains the durable coordination and
gallery store.

Cloud Run functions may be useful for small event handlers or notifications, but they
should not be the primary media-upload endpoint: current HTTP function request limits
are too small for the supported videos. A Cloud Run service is more suitable for the
API and upload-session broker, while direct browser bytes should flow through a GCS
resumable upload session rather than through the application container.

### Technical implementation

Deploy a stateless `face-console` container with minimum instances initially zero,
bounded maximum instances, and Direct VPC egress to Cloud SQL. Serve the static UI from
the same service for the first version. Manage its runtime identity, Secret Manager
access, Cloud SQL connection, GCS permissions, configuration, and invocation policy in
Terraform.

The service invokes `face-ingest-drain` for CPU retrieval and the existing GPU job for
inference, then returns immediately; it performs neither task inside an HTTP handler.
The UI polls `GET /api/runs/{run_id}` with exponential backoff
and stops on `awaiting_face_selection`, success, failure, or expiry. Set `Cache-Control:
no-store` on run, preview, candidate, and gallery responses.

## Media storage design

Use separate prefixes and lifecycle rules:

- `submissions-temporary/`: private uploads awaiting or undergoing processing, with a
  short fail-safe lifecycle;
- `training-media/`: explicitly retained, immutable source media;
- `subject-gallery/`: retained representative face crops derived from enrolled media;
  and
- `face-staging/`: existing short-lived worker staging.

Each submission must record the exact bucket, object name, generation, SHA-256, byte
length, content type, encryption mode, retention policy, and deletion state. Promotion
from temporary to retained storage should be a generation-pinned server-side copy. A
discard decision should delete only the exact temporary generation after processing
reaches a recorded terminal state.

The current archive uses a CSEK. That is workable while trusted backend services obtain
the key from Secret Manager. Team users must never enter, upload, download, or otherwise
receive the CSEK at any step.

For a direct file upload, the authenticated Cloud Run API should initiate a resumable
GCS upload with the CSEK headers server-side and return only the scoped session URI to
the browser. Cloud Storage treats that session URI as a bearer credential, so it must be
treated as a secret, transmitted only over HTTPS, bound to a random object name, and
never logged. Cloud Storage sessions can remain usable for up to one week; hiding the
URI in the UI does not revoke it. The browser uploads bytes to that session without
learning the CSEK.

For a submitted link, the backend fetcher writes the bytes with the Secret Manager CSEK.
For processing and later evidence review, dedicated backend identities retrieve the key
from Secret Manager and decrypt only authorized object generations. End-user IAM should
not grant Secret Manager access or direct CSEK-object read access.

### Technical implementation

Use random, server-generated object names:

```text
submissions-temporary/<run_id>/source.<ext>
submissions-temporary/<run_id>/groups/<group_id>/<preview_id>.jpg
training-media/<source_id>/source.<ext>
subject-gallery/<subject_id>/<representative_id>.jpg
```

Add prefix-scoped IAM and lifecycle rules independently. Temporary objects receive a
short fail-safe lifecycle; retained training and gallery prefixes must not inherit it.
Every write uses `if_generation_match=0`; every read, promotion, or deletion supplies
the recorded generation. Verify CSEK metadata, size, and SHA-256 before advancing.

The API initializes resumable sessions with the Secret Manager CSEK and forwards the
browser's validated `Origin` on initiation. Configure bucket CORS for only the console
origin and required upload methods and headers. Never log or persist the session URI.
The browser may cancel an incomplete session with `DELETE`; otherwise it expires at the
provider limit.

After upload, the browser calls `POST /api/runs/{run_id}/upload-complete` with the
expected byte length and SHA-256. The API reloads the random object, records its
generation, verifies size and encryption metadata, and changes `awaiting_media` to
`queued`. The ingestion worker verifies SHA-256 while streaming before detection; a
mismatch fails the run and schedules exact-generation deletion. Runs not finalized
within seven days expire. Only completed uploads create GCS objects, so lifecycle
cleanup covers completed orphans; incomplete sessions are cancelled by the browser or
expire within one week.

### Retention policy

- Delete `search_then_discard` media and temporary previews immediately after terminal
  processing; expire runs left awaiting media or face selection after seven days.
- Retain candidate results and detailed run status for seven days after terminal
  completion, then remove them and their temporary presentation artifacts.
- Retain ordinary sanitized service logs for 30 days.
- Treat `retain_and_enroll` initially like the existing training corpus: keep the CSEK
  encrypted source material, Cloud SQL embeddings/tracks and subject contribution, and
  CSEK-encrypted representative gallery faces.
- Source-media retention is independent from derived-model retention. Enrolled source
  media has no automatic expiration and remains until an internal user explicitly
  removes it. After removal, keep its embeddings, tracks, subject contribution,
  representative gallery faces, and provenance metadata.
- Representative faces follow the subject/model lifecycle, not the source object's
  retention period. Recompute them only for model correction, subject merge/split, or a
  separate gallery-retention decision.

Use a seven-day GCS lifecycle as fail-safe cleanup only on the temporary prefix. Do not
apply it to training media or subject-gallery objects.

Use a daily Cloud Scheduler invocation of the shared CPU job's `maintenance` mode as
the cleanup authority. It expires stale runs, deletes expired candidate rows and
temporary previews/media by exact generation, and retries `promotion_pending` and
`deletion_pending` operations. Lifecycle rules remain a fail-safe, not the source of
SQL state transitions. A reconciliation report lists items still failing after bounded
retries.

Deduplicate retained source storage by SHA-256 while keeping every `media_submission`
distinct. A retained submission with an identical digest reuses the available
generation-pinned `source_asset`; otherwise it creates a new retained source. Do not use
the existing `(external_source_ref, source_sha256)` uniqueness rule as the upload
identity because uploads have no stable external reference. Enrollment remains tied to
the submission's selected face groups—two submissions of the same multi-person media
may intentionally select different people—and its per-group idempotency keys prevent a
retry of one submission from contributing twice. `search_then_discard` runs never claim
or mutate a retained source merely because their digest matches.

## Face-group selection and enrollment

Images and videos can contain multiple people, so the user should decide which detected
faces are actually used. Detection and temporary tracking may inspect the entire input
to build the selection screen, but unselected groups must not be searched, returned as
candidates, or enrolled.

For an image, show a numbered bounding box and crop for each detection. For a video,
show a numbered track with a best crop, a small contact sheet, and its time range. The
UI should provide **select all**, **clear all**, and individual controls. A single
high-quality group may be preselected for convenience, but the user must confirm the
selection before matching begins.

If `search_then_discard` is selected, only the confirmed groups are searched. If
`retain_and_enroll` is selected, the complete encrypted source media is retained as
training evidence, but only the confirmed groups contribute embeddings to the gallery.
Temporary crops, tracks, and embeddings for unselected groups are deleted after the run.
At least one face group must be selected to continue.

### Technical implementation

Refactor processing into two retryable stages:

1. `detect_groups` runs SCRFD, ByteTrack for video, and quality selection. It writes
   small CSEK-encrypted preview crops/contact sheets and group metadata without gallery
   mutation.
2. `match_selected_groups` loads only confirmed groups, creates AdaFace embeddings,
   performs the read-only candidate query, snapshots candidates, and enrolls when the
   handling policy requires it.

Invoke the same GPU Job definition once for `detect_groups`; it exits after recording
`awaiting_face_selection`. Confirmation invokes it again for
`match_selected_groups`. This second allocation is a Phase 1 cost and latency gate, not
an assumed acceptable path. Benchmark CPU embedding of the small selected-crop set as
the fallback; if it meets the two-minute image-stage target, keep GPU detection but run
selected-crop embedding and matching in `face-ingest-drain` to avoid a second GPU start.

`submission_face_group` stores a random `group_id`, run ID, bounding box or time range,
quality summary, preview generation references, selection state, and model versions.
The browser returns only server-generated group IDs, never coordinates or object paths.
Freeze selection when matching begins. Delete unselected previews and intermediate
crops by exact generation after completion or selection expiry.

For each selected retained group, enrollment is automatic:

- attach it to the top model-compatible subject when the calibrated matching rule
  passes; or
- create a new anonymous subject when the rule does not pass.

The UI does not ask the user to confirm subject attachment because full source evidence
is not yet available there for a better comparison. Candidate rankings must still show
the pre-enrollment result and automatic outcome. Similarity does not assert a real-world
identity, and later merge/split tools must repair incorrect clustering.

Automatic enrollment is disabled until all Phase 2 gates pass. The threshold version
must be calibrated and approved rather than `unvalidated-v1`; write-path matching must
filter subjects by embedding model version; a matched subject's canonical embedding
and `sample_count` must update atomically; subject creation must be race-safe; and a
tested manual merge/split correction command must exist before the UI can mutate the
gallery. Updating a canonical embedding uses a normalized, sample-count-weighted mean
under a subject-row lock and records the enrolled group exactly once.

## Candidate response

Candidate results should retain the current useful behavior:

- deterministic top-K compatible subjects;
- three to five representative face crops for each candidate;
- cosine similarity and model version;
- nullable operator-managed display name;
- source observation count;
- source asset reference and video time range; and
- an opaque evidence reference rather than a raw GCS URL.

The response must distinguish candidate ranking from the automatic enrollment outcome
and from a real-world identity assertion. Those are three different results.

### Technical implementation

Snapshot pre-enrollment candidates in `candidate_result`, keyed by run ID, group ID,
rank, and subject ID. Apply the same model-version filter and deterministic similarity
then UUID ordering as the local probe. Default to top 10 with a server maximum. Limit
source provenance to a small server-side count in the initial response. Return total
counts and a separate bounded subject-detail response; add cursor pagination only if
real subjects exceed useful response sizes.

Candidate JSON includes `representative_faces`, each with an opaque endpoint such as
`/api/gallery/faces/{representative_id}` and a small quality/source summary. It contains
no GCS URLs or embeddings.

## Initial visual subject gallery

The initial team UI must include a visual subject gallery. Subject UUIDs and provenance
alone are not efficient review tools. Each candidate card should show the subject ID,
nullable display name, similarity, source/observation counts, and three to five
representative enrolled-face crops. Selecting a card should open a subject page with
additional representative faces and provenance.

Representative crops are derived only from retained, enrolled media. Choose a small
high-quality and visually varied set across distinct sources or times rather than
keeping every crop. Store the crops as CSEK-encrypted objects under a dedicated prefix
such as `subject-gallery/`; store their exact generation, subject, source track,
timestamp, quality, and active status in Cloud SQL. The authenticated backend serves
them to the UI without exposing the CSEK or a durable GCS URL.

When better observations arrive, update the active representative set. Subject merges
and splits must recompute its membership so a crop never remains displayed for the
wrong subject. Old crop objects can be removed by exact generation after they are no
longer referenced and the applicable cleanup delay has passed.

Gallery replacement is publish-then-retire. Upload every new crop first and insert it
as inactive. In one Cloud SQL transaction, activate the complete new set and deactivate
the old set. Gallery reads resolve only active generation-pinned rows, so the response
never points at an object deleted before publication. The maintenance job deletes
retired objects after a short grace period; failed deletion is harmless and retryable.

Existing subjects need a one-time backfill before the team UI is considered ready. The
current `face_track` rows do not contain per-observation crop timestamps or boxes, so a
backfill cannot simply retrieve an already identified best frame. Retrieve each
generation-pinned retained source and rerun detection/tracking within the stored track
time ranges. Associate a regenerated track to the existing row only when its time-range
overlap and model-compatible aggregate-embedding similarity produce one unambiguous
winner; ambiguous or missing matches are skipped and reported, never reassigned. Choose
the best regenerated observation and write the gallery record without changing the
existing subject. Measure scan cost and successful-association rate on a sample before
the full backfill. The current locally retained review directories are not a complete
or authoritative gallery source.

Incoming face-selection previews and gallery representative crops serve different
purposes. Submission previews are temporary and show what the user may select. Gallery
crops are retained evidence derived from enrolled material and appear on future
candidate cards.

### Technical implementation

Create `subject_representative_face` with:

```text
representative_id, subject_id, source_id, face_track_id,
object_name, object_generation, source_timestamp_ms,
quality_score, active, created_at
```

Index `(subject_id, active, quality_score DESC)` and require each object generation to
be unique. Select representatives deterministically from high-quality tracks, limit any
one source, and favor source/time diversity. Start with five active crops per subject
and recompute active membership when subjects merge or split.

`GET /api/gallery/faces/{representative_id}` resolves the exact active generation and
streams `image/jpeg` with `Cache-Control: private, no-store`. Implement backfill as a
mode of the same interactive GPU Job, committing one subject at a time and skipping
subjects already having a sufficient active gallery.

## Candidate-evidence review

The current database already connects candidate subjects to `face_track`, `source_asset`,
and video time ranges. That provenance is the foundation for later evidence retrieval.
What is missing is a team-safe retrieval boundary.

Add an authenticated evidence service that accepts an opaque candidate/observation
reference, verifies that the requester is an approved internal user, and then reads the
exact encrypted source generation using its runtime identity. Prefer returning a
short-lived streamed clip or face-centered review image around the recorded timestamps
as the first view. The same internal role may then open or download the complete source.
The subject page provides all three formats: face crop, short contextual clip, and full
source media.

The service must:

- keep the CSEK capability server-side;
- never expose decrypted GCS objects or durable public URLs;
- prevent path or generation substitution;
- avoid caching plaintext beyond a short processing window; and
- return `404` when the recorded source generation has been removed or is unavailable.

This feature should follow, not precede, the submission authorization and retention
model. Evidence cannot be regenerated if media was discarded initially or an internal
user later deleted the retained source, so the candidate response should mark it
unavailable and the evidence endpoint should return `404`.

### Technical implementation

Reserve `GET /api/observations/{track_id}/crop`, `/clip`, and `/source` for Phase 3.
Resolve objects exclusively through database provenance, pin the recorded generation,
and reject client-supplied object names. Generate bounded clips in memory or encrypted
temporary storage and stream with `Cache-Control: no-store`. Do not issue general-purpose
signed read URLs for CSEK objects. Default the UI to crop, then clip, while keeping a
clear full-source action on the same observation page.

## Data-model evolution

Add new tables rather than overloading `processing_rollout` or pretending an interactive
submission is a historical manifest rollout:

- `media_submission`: run ID, submitter, purpose, source kind, handling policy, status,
  media metadata, policy version, and timestamps;
- `submission_face_group`: one image face or video track and its processing/model
  provenance;
- `candidate_result`: bounded ranked candidates and scores with an expiry policy;
- `subject_representative_face`: subject, encrypted crop object generation, source
  track/time, quality, and active-set metadata.

Store the automatic attach/create outcome, matching inputs and versions, and resulting
subject directly on `submission_face_group`. A separate enrollment-history table is not
needed while attachment is automatic and history/auditing is out of scope.

Retained media can continue to use or reference `source_asset`. The schema should make
the relationship between a submission, retained source, processing job, face tracks,
and subjects explicit. Removing a GCS source must not delete the `source_asset`, tracks,
subject contribution, or gallery faces. Instead, mark the source unavailable while
preserving its URI, digest, generation, timestamps, and derived lineage.

### Technical implementation

Ship additive SQL migrations while preserving current tables. Use `ON DELETE RESTRICT`
for retained lineage, check constraints for valid state/policy combinations, and partial
unique indexes for active leases and representative sets. Store source URLs and object
metadata separately from user-visible labels.

Add `source_asset.media_state`, `media_deleted_at`, and an optional sanitized deletion
reason. The exact-generation deletion worker changes `media_state` from `available` to
`deletion_pending`, deletes the CSEK object, and then records `deleted`; retries treat an
already absent generation as success. Evidence lookup returns `404` for any state other
than `available`, while gallery queries continue using the preserved derived rows.

Do not add a generalized storage-action outbox initially. Represent the few cross-system
operations explicitly with states such as `promotion_pending`, `promoted`,
`deletion_pending`, and `deleted`. Idempotent workers and a reconciliation command retry
or repair those states using exact object generations. Introduce an outbox only if
observed failure modes justify the added abstraction.

## Team security and role

Use one application role for now: `internal_user`. Membership is limited to the
internal security team and specifically approved senior employees. Every member may:

- submit media and view runs;
- choose `search_then_discard` or `retain_and_enroll`;
- select face groups;
- manage subject corrections and identity labels; and
- use candidate-evidence and retained-source review when those features are added.

Do not build a permission matrix until actual team use demonstrates a need. The service
still requires authenticated principals, an approved-user allowlist, and CSRF/rate/size
protections. Do not expose Cloud SQL, the CSEK, or general GCS credentials to browsers.
Give the browser only a narrowly scoped resumable-upload session URI, and keep backend
runtime permissions out of end-user credentials.

### Technical implementation

Enable Identity-Aware Proxy directly on `face-console` and grant access to one Google
Group mapped to `internal_user`. Members may use Google Workspace identities, Gmail
identities, or Google Accounts created with an existing address such as Proton Mail.
If any member is outside the project's Google organization, configure IAP's external
OAuth client once. Derive the principal from verified IAP identity, never a request-body
field. Every approved group member passes the same application authorization check.

Before deployment, inspect the actual project's organization and OAuth configuration
and complete an end-to-end IAP login with one intended Proton-backed Google Account.
Treat external-user access as deployment-verified, not guaranteed solely by the design.

For cookie sessions, use secure, HTTP-only, same-site cookies and CSRF tokens on
mutations. Validate Origin, rate-limit creation and status polling, cap concurrent active
runs per user, and sanitize errors. The one new console/ingestion service account
receives only its required Secret Manager, Cloud SQL, Cloud Run invocation, and
prefix-scoped GCS permissions. The existing GPU identity retains inference permissions.

## Concurrency and correctness work

The current single-running-rollout constraint protects subject creation from races but
would block interactive team use. Before scaling beyond one worker:

1. make subject matching and canonical-embedding updates transactionally safe under
   concurrent submissions;
2. define deterministic behavior when two workers create a subject for the same person;
3. separate bulk rollout scheduling from latency-sensitive interactive submissions;
4. add per-submission idempotency keys and retry-safe media promotion/deletion; and
5. load-test Cloud SQL, pgvector indexing, GPU concurrency, and queue fairness.

Do not increase parallelism until duplicate-subject and lost-update tests pass.

### Technical implementation

Lock the chosen candidate subject row before updating its canonical embedding and sample
count. Give each selected group a stable enrollment idempotency key and unique
constraint. Before creating a new subject, repeat candidate lookup inside the write
transaction or use a bounded creation lock to prevent concurrent duplicate subjects.

Use separate queue priorities for interactive stages and bulk drains. Initially retain
one enrollment writer even if detection and matching gain parallelism. Integration tests
must cover lookup/commit races, expired leases, retries after completion, and concurrent
requests with the same idempotency key.

### Initial service objectives

Treat these as internal targets to measure and revise, not external SLAs:

- 95% of ordinary UI/API requests complete within two seconds.
- Run creation returns a `run_id` within two seconds.
- A persisted state change appears in the polling UI within five seconds.
- Image face selection is ready within two minutes, and candidates are ready within two
  minutes after selection.
- Interactive video processing begins within two minutes when capacity is available;
  candidates are ready within the media duration plus five minutes, excluding time
  waiting for user face selection.
- Each user may have up to three queued or active interactive runs initially.
- Interactive work has queue priority over bulk work but does not preempt media already
  processing. Bulk parallelism stays at the currently validated value until load tests
  support an increase.

Expose stage timestamps and queue delay in internal metrics so retrieval, GPU wait,
detection, user wait, matching, and enrollment can be evaluated separately.

## Delivery phases

### Phase 0 — finish the current foundation

- Reconcile the active historical-video rollout.
- Calibrate subject matching and document a manual database-backed correction procedure.
- Define independent source-media, derived-model, and backup retention policies.

### Phase 1 — managed search for a small team

- Add the authenticated UI and create-run/check-run/status/result endpoints.
- Make direct HTTPS media, JustPaste, and Luluvid links the primary inputs; reject
  HeyLink and unsupported arbitrary HTML pages.
- Support direct JPEG, PNG, and MP4 upload through backend-initiated resumable sessions.
- Add the `awaiting_face_selection` UI for image detections and video tracks.
- Backfill representative crops for existing subjects into CSEK-encrypted gallery
  storage.
- Show representative faces on candidate cards and subject pages.
- Run managed candidate search without enrollment.
- Hide the retention control and reject `retain_and_enroll` at the API boundary.
- Return a copyable run ID immediately and delete `search_then_discard` media after
  terminal processing.
- Add seven-day result retention, 30-day operational logging, quotas, and rate limits.

### Phase 2 — explicit retained enrollment

- Add retained `training-media/` storage and immutable `retain_and_enroll` records.
- Enroll only user-selected image faces and video tracks for retained submissions.
- Automatically attach qualifying selected groups or create new anonymous subjects.
- Implement concurrent-safe subject updates. Defer a dedicated merge/split UI until
  actual correction volume justifies it.
- Add explicit source-deletion/tombstone handling without deleting derived model
  lineage.

### Phase 3 — candidate-evidence review

- Add opaque evidence references to candidate responses.
- Add authorized generation-pinned clip/image streaming.
- Add full-source retrieval alongside crop or short-clip review for the internal role.

### Phase 4 — measured scaling

- Introduce fair scheduling between interactive and bulk work.
- Increase GPU and database concurrency only from measured demand.
- Re-embed retained training media when a model migration is approved.
- Add a subject merge/split UI only if the manual correction procedure has become an
  operational burden.

### Technical acceptance gates by phase

- Phase 0: current unit/integration suites pass, production reconciliation is clean,
  and the matching evaluation records an approved threshold version.
- Phase 1: a link and direct upload both reach `awaiting_face_selection`; selected
  groups return the same ranking as the local probe; discarded media and previews are
  removed; run lookup survives browser/session loss; and representative galleries exist
  for all candidate subjects used in acceptance tests. Arbitrary-host fetching remains
  disabled until connection-time address binding passes the SSRF matrix. Browser upload
  acceptance covers finalize, checksum mismatch, cancel/abandon, configured CORS, and
  the actual project's IAP flow for an intended external account.
- Phase 2: retained media is CSEK-encrypted and generation-pinned, only selected groups
  contribute exactly once, candidates reflect the pre-enrollment gallery, the calibrated
  and model-version-filtered rule deterministically attaches or creates, matched
  canonical embeddings and sample counts update atomically, concurrent creation is
  race-safe, a manual merge/split repair command is tested, and retries cannot duplicate
  a sample or subject update.
- Phase 3: crop, clip, and full-source endpoints cannot substitute object names or
  generations, never expose CSEK/GCS credentials, and leave no plaintext artifact after
  their cleanup window.
- Phase 4: measured latency, queue fairness, database load, duplicate-subject rate, and
  cost support each concurrency increase before Terraform changes are applied.

## Decided product behavior

Authentication is decided: use Cloud Run IAP with one approved Google Group mapped to
`internal_user`; Google Accounts backed by Proton addresses are supported.

Retention is decided: unfinished temporary runs and candidate results expire after
seven days and ordinary sanitized logs remain for 30 days. Derived model data and
representative gallery faces remain durable. Enrolled CSEK source media has no automatic
expiration and remains until explicitly deleted by an internal user.

Enrollment attachment is decided: selected retained groups attach automatically when
the calibrated matching rule passes and otherwise create a new anonymous subject. The
initial UI does not require subject-attachment confirmation.

Source deletion semantics are decided: explicitly deleting encrypted training media
removes the exact source generation but preserves embeddings, tracks, subjects,
representative gallery faces, and provenance metadata. Evidence references may return
`404` afterward. There is no scheduled deletion of enrolled source media.

Evidence formats are decided: the subject UI provides representative gallery faces,
observation crops, short contextual clips, and complete retained source media to the
same `internal_user` role. All decryption remains in the backend.

Performance objectives are decided: two-second UI/run-creation targets, five-second
state visibility, two-minute image-stage targets, video duration plus five minutes,
three active or queued runs per user, and interactive queue priority without preemption.

Link support is decided: accept direct HTTPS image/video links from validated public
hosts, direct JustPaste links, direct Luluvid links, and file uploads. Reject HeyLink and
unsupported arbitrary HTML pages. Keep JustPaste/Luluvid modular and revisit it if usage
or reliability no longer justifies maintenance. Arbitrary-host support is conditional
on the connection-time SSRF gate; adapters and upload can ship first.

Run visibility is decided: all members of the one approved group may view all runs.
Phase 1 policy exposure is decided: search-only is exposed; retention is rejected until
Phase 2. CPU ingestion, upload finalization, maintenance ownership, deduplication, and
gallery replacement are specified above. No unresolved product choice blocks interface
design, but the bounded technical proofs below block enabling their dependent features.

Before team launch, name one internal owner to acknowledge the intended collection use
and the already-decided deletion behavior: deleting source media can preserve face
embeddings and representative gallery faces. This is a launch sign-off, not a new audit
service, role system, or governance workstream.

### Technical review artifacts

Capture the decided behavior in small reviewable artifacts: a retention/lifecycle table,
evidence endpoint wireframes, a load-test target, and a source-adapter validation matrix.
Keep these synchronized with this plan during implementation.

## Recommended immediate next step

Design Phase 1 as a narrow authenticated, link-first Cloud Run service while leaving the
current local probe available for development. Specify the create-run/check-run API,
temporary CSEK media lifecycle, backend-initiated resumable upload, short-lived result
retention, face-selection interaction, and single-role access model first. Phase 2 then
makes the UI's retention choice an explicit `retain_and_enroll` policy so every retained
submission grows the recoverable training corpus while only user-selected faces grow
the gallery.

### Initial implementation deliverables

The first implementation change should contain only additive database migrations,
OpenAPI request/response schemas, run-state transition tests, Terraform stubs for
`face-console`, and static UI wireframes for submit, check-run, face selection, and
candidate gallery views. Review those interfaces before implementing remote media fetch
or gallery mutation.

Before that review closes, complete these bounded checks:

1. prove a browser resumable upload works when the backend applies the CSEK without
   exposing it, including Origin/CORS, explicit finalization, checksum failure,
   cancellation, and abandonment;
2. prove the arbitrary-URL transport binds the connection to the validated public IP
   and rejects DNS rebinding, IPv4-mapped IPv6, redirect-to-private, and DNS-change test
   cases;
3. measure the second GPU allocation required by detect-then-select-then-match and the
   CPU selected-crop fallback;
4. sample the representative-gallery backfill to measure cost and unambiguous track
   reassociation rate;
5. calibrate the automatic subject-attachment rule and test model-version filtering,
   canonical updates, creation races, idempotent retries, and manual merge/split repair;
6. verify IAP against the actual project with the intended group and an external
   Proton-backed Google Account; and
7. finish and reconcile the current historical rollout.

## Platform references

- Cloud Storage resumable uploads and delegated session URIs:
  https://docs.cloud.google.com/storage/docs/resumable-uploads
- Cloud Storage resumable-upload cancellation:
  https://docs.cloud.google.com/storage/docs/performing-resumable-uploads
- Cloud Storage CORS configuration:
  https://cloud.google.com/storage/docs/configuring-cors
- Cloud Storage CSEK upload requirements:
  https://docs.cloud.google.com/storage/docs/encryption/using-customer-supplied-keys
- Cloud Run Job execution:
  https://docs.cloud.google.com/run/docs/execute-jobs
- Scheduled Cloud Run maintenance:
  https://docs.cloud.google.com/run/docs/triggering/using-scheduler
- Cloud Run service request timeouts:
  https://docs.cloud.google.com/run/docs/configuring/request-timeout
- Cloud Run service and function request-size limits:
  https://docs.cloud.google.com/run/quotas
- Identity-Aware Proxy for Cloud Run:
  https://docs.cloud.google.com/run/docs/securing/identity-aware-proxy-cloud-run
