# Phase 1 review artifacts

This document records the bounded operational decisions, deployed state, and remaining
acceptance evidence for the managed-search phase. The temporary single-account console
deployment is enabled; team launch remains gated by the items called out below.

## Retention and lifecycle

| Data | Location | Encryption | Lifetime | Deletion owner |
| --- | --- | --- | --- | --- |
| Uploaded or fetched search media | `submissions-temporary/<run>/` | GCS CSEK | Delete after a terminal run; seven-day bucket rule is the fail-safe | `face-ingest-drain` maintenance |
| Face-selection previews | `submissions-temporary/<run>/previews/` | GCS CSEK | Delete after a terminal run; seven-day bucket rule is the fail-safe | `face-ingest-drain` maintenance |
| Run, selection, and candidate rows | Cloud SQL | Platform encryption | Seven days after creation | Scheduled maintenance transaction |
| Representative gallery faces | `subject-gallery/<subject>/` | GCS CSEK | Durable; replaced generations receive a grace period | Gallery publication and maintenance |
| Subject embeddings and provenance | Cloud SQL | Platform encryption | Durable | Explicit future administrative workflow |
| Sanitized application logs | Cloud Logging `_Default` | Platform encryption | 30 days | Logging bucket retention |

Phase 1 never stores source URLs after run expiry and rejects
`retain_and_enroll`. Deleting source media does not imply deletion of durable derived
embeddings or representative gallery faces.

## Source-adapter validation matrix

| Input | Phase 1 behavior | Validation evidence |
| --- | --- | --- |
| Direct JPEG, PNG, MP4 HTTPS URL | Allowed only when the arbitrary-host gate is enabled | `test_secure_fetch.py`: public resolution, pinned connection, signatures, bounds, redirects |
| JustPaste page | Extract exactly one supported Luluvid link | `test_source_adapters.py` |
| Luluvid page | Extract a supported static media URL, including packed-player form | `test_source_adapters.py` |
| HeyLink or other HTML page | Reject with a stable source error | `test_source_adapters.py` and API source validation tests |
| Private, loopback, link-local, reserved, or mixed DNS | Reject before connection | `test_secure_fetch.py` |
| DNS change, rebinding, IPv4-mapped IPv6, redirect to private address | Reject while resolving every hop and connect only to the validated address | `test_secure_fetch.py` |

Arbitrary-host fetching remains disabled in Terraform until the same matrix is run
against the deployed transport.

## UI and evidence wireframes

The executable wireframes are `ui/index.html`, `ui/styles.css`, and `ui/app.js`:

1. Submit an HTTPS link or direct upload and immediately receive a copyable run ID.
2. Recover any run through `/runs/<uuid>` or the check-run view.
3. Poll status, cancel eligible work, and retry only server-declared retryable failures.
4. Select one or more detected image faces or video tracks.
5. Review ranked candidate cards with representative faces and open a subject gallery.

Candidate evidence crops, clips, and retained full-source retrieval are deliberately
deferred to Phase 3. Phase 1 gallery URLs contain opaque database IDs and resolve the
stored object name and exact generation server-side.

## Initial load target

The launch target is a small internal team with at most three active or queued runs per
principal. The console is capped at three instances, 20 requests per instance, ten
create requests per principal per minute, and 120 read requests per principal per
minute. GPU jobs remain single-task and single-parallelism.

Before increasing any limit, record a test with two concurrent principals covering:

- create response in at most two seconds and state visibility in at most five seconds;
- an image detection stage in at most two minutes;
- a representative video completing within video duration plus five minutes;
- no duplicate operation claims or candidate rows during retries;
- Cloud SQL connection utilization, GPU allocation time, and per-run cost.

## Deployment evidence

- `migrations/001_phase1_runs.sql` was applied to the actual Cloud SQL database on
  2026-09-02. All seven Phase 1 tables and CRUD grants for
  `jack@jackstruck.info`, `face-batch-console@teak-banner-dome.iam`, and
  `face-batch-runtime@teak-banner-dome.iam` were verified.
- Console image built, smoke-tested, and deployed as
  `us-central1-docker.pkg.dev/teak-banner-dome/face-batch-worker/console@sha256:dc6b3f363160aad109ea4cb52381c7051996bb32d34f075aafe653e596c7a8c1`;
  GPU image built with checksum-verified models, packaging-tested, smoke-tested, and
  deployed for interactive work as
  `us-central1-docker.pkg.dev/teak-banner-dome/face-batch-worker/worker@sha256:f1a5248637f471d811d2254bde1e3fe3f8cb2e4d8109691e71a1ef9f0ffbb6f0`.
  The active bulk rollout remains isolated on its original
  `sha256:9c2b88bfa229f3e542dd71d5cbdc2ff7882d2df90e983ea439f56d28d12f5d2f`
  worker digest. A live API read confirmed all four job/service image pins after
  deployment, and Terraform reported no drift.
- Local verification on the deployed source revision completed with 115 passing tests,
  targeted Ruff checks, JavaScript syntax validation, Python bytecode compilation,
  Terraform formatting/validation, and `git diff --check`. No browser-level unit test
  was added.
- Superseded ThunderCloud images and all unused build cache were removed after the
  immutable images were pushed. Codespace storage ended with 17 GB free (46% used),
  and the temporary Artifact Registry login credential was removed.

## Team-launch evidence still required

- Complete the link and browser-upload journeys through actual IAP as
  `jack@jackstruck.info`, including finalize, checksum mismatch, cancel, abandoned
  upload cleanup, run lookup after session loss, and exact-origin CORS in the browser.
- Run the deployed connection-pinning/SSRF matrix before enabling arbitrary public
  hosts. A bounded direct-JPEG probe was started but explicitly cancelled before its
  fetch work was claimed; run `0404118a-0da4-4150-a141-addc8d0ed997` and execution
  `face-ingest-drain-gdr7m` are cancelled and are not acceptance evidence.
- Exercise a supported JustPaste or Luluvid link through IAP until it reaches
  `awaiting_face_selection`, then confirm that its selected-group ranking matches the
  local probe.
- Replace the temporary `user:jack@jackstruck.info` IAP principal with an approved
  Google Group and validate membership before team launch.
- Complete representative coverage for every candidate subject actually used in the
  remaining link/browser acceptance tests. The bounded sample below covers 22 subjects,
  not the full 7,552-subject historical gallery.
- Record the internal owner's acknowledgment of collection use and deletion semantics.

### Bounded gallery sample — 2026-09-03

Cloud Run execution `face-interactive-gpu-9n7gs` completed successfully on one L4:

| Metric | Result |
| --- | ---: |
| Allocation/import time | 1m 51s |
| Total execution time | 13m 7s |
| In-container elapsed time | 661.872s |
| Subjects/tracks scanned | 25 / 25 |
| Source bytes scanned | 705,312,275 |
| Unambiguous associations | 22 (88%) |
| Ambiguous/skipped | 3 |
| Source failures | 0 |
| Subjects published | 22 |

Post-run verification found 22 active representatives across 22 distinct subjects and
sources, all with pinned positive object generations. The first attempted sample
correctly exposed an unsupported GCS client checksum option before publishing any
gallery data; transport checks now use the client's supported automatic checksum while
application-level SHA-256 verification remains mandatory.

### Upload, match, and cleanup acceptance — 2026-09-03

Run `ab5bd05d-a465-4915-8e7c-eb8d1e6ea4f8` used a real CSEK resumable PNG upload,
reached `awaiting_face_selection`, froze one selected group, and completed a separate
L4 match execution with ten ranked candidates. The upload request returned the exact
configured CORS origin. Both its source and preview were queued with positive pinned
generations; maintenance execution `face-ingest-drain-p599z` completed successfully,
both cleanup rows became `succeeded` on attempt one, and direct generation-qualified
GCS metadata requests returned 404 for both objects.

Cloud Run override callbacks use custom role `facePhase1JobInvoker`, containing only
`run.jobs.run` and `run.jobs.runWithOverrides`, for the console and GPU identities.
Terraform was zero-drift after deployment. The Phase 1 interactive image is now
independently pinned so updating it cannot change the active bulk job definition.
