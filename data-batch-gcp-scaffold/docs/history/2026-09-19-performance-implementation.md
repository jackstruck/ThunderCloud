# Platform and import performance implementation

Implementation and pilot verification are complete. The reviewed implementation
plan was removed from the checkout during the 2026-10-11 documentation cleanup;
its tracked original remains in Git history. This record contains the release,
verification, and capacity decisions.

## Platform software release

Console revision `face-console-00062-wlp` serves the query and connection-pooling improvements on the original `db-f1-micro` tier. Console resources are 1 CPU / 1 GiB, two Gunicorn workers with four threads each, Cloud Run concurrency eight and maximum three instances. Each process has a pool of two connections, for a console ceiling of twelve database connections. CPU job processes default to one connection each. GPU workers use two connections because gallery publication commits a cleanup receipt independently of the enrollment transaction.

Changes select source/subject pages before aggregating examples where filter/sort semantics allow it, select a bounded matching member set before aggregating all its examples, and fetch candidate metadata in batches. SQLAlchemy pools reuse IAM-authenticated pg8000 connections, clear transaction/session state, replace disconnected sessions, and bound checkout waits. Gallery access and response policy are preserved.

### Initial live comparison

Same requests, three samples per ordinary endpoint, with the existing import active. Values below are medians in seconds; this is a small comparison, including initial connection setup.

| Request | Before | After software |
| --- | ---: | ---: |
| Sources, 24 records | 5.235 | 0.154 |
| Subjects, 24 records | 5.763 | 0.223 |
| Gallery image | 1.699 | 0.298 |
| Source scan, one sample | 6.410 | 0.359 |

A subsequent 32-request mixed workload with four concurrent clients returned HTTP 200 throughout. Source requests ranged up to 0.419 seconds, subjects up to 0.431 seconds. Gallery median was 0.269 seconds, with two approximately 10.4-second outliers retained in the evidence.

A read-only database profile on source `046c24da-e49d-4155-a32f-135805024975` loaded 32 members in 0.367 seconds. The rewritten member aggregate took 0.124 seconds and used the existing source/subject indexes. This supports retaining those indexes for now rather than adding an additional example index. Private measurements and query plans are under `.local/performance/` at repository root.

## Import implementation and rollout

Local changes add a durable PostgreSQL import queue, idempotent receipts, leased dispatch, a shared ingestion-drain lease, global processing admission, an interactive slot reservation, pending/review limits, bounded retries and recovery of missed GPU invocations. Acquisition tails its manifest and exits after durable handoff. Batch status reports queued work separately from completed processing.

The migration adds queue tables, processing workload attribution and scoped archive lookup indexes. Migration 004's original comment was restored from commit `5cc251b` to match its installed checksum; its SQL statements are unchanged. This preserves the existing migration ledger and lets the normal migration runner validate it.

Initial validation: 157 checks passed in the combined run, with 22 browser-only cases skipped; one database-copy compatibility issue was then fixed. The subsequent focused queue/copy/lifecycle checks passed (11), and migration checks passed (12). Further rollout and runtime verification remain in progress.

Final runtime, throughput/cost, completion-estimate and audit evidence appears below.

## Live rollout and verification

Migration 027 applied through the normal migration runner; runtime grants include both new queue tables. The database resize operation completed successfully with `db-g1-small` and zonal availability. The first post-resize memory reading was 48.4%, compared with the earlier 100% readings on `db-f1-micro`. Ordinary endpoint medians immediately after restart were 0.145 seconds for sources, 0.201 for subjects, and 0.297 for gallery images; the source scan took 0.295 seconds.

The final console revision is `face-console-00065-8sz`. Console and ingestion use image digest `4b467e60ce9403df1729caca6c833dde2a9ac2a1142f796721a4fc0ed0ce6a21`; acquisition uses `3c9e5d9219f004c3ce7adca3db8e386b2ca871f58057de5077b8c1d3760ba1e3`; GPU processing uses `404965f2d7d5777ae4a1aa0a0e8c77b51a47e8f164d01bdddd93c4fc1e3316a9`. The GPU template explicitly sets `FACE_DB_POOL_SIZE=2`. Ignored deployment tfvars were synchronized with these releases and the database tier; prior images remain listed for rollback. Scoped API updates preserved unrelated live configuration.

A 100-request mixed check with four concurrent clients during import processing returned HTTP 200 throughout:

| Request | Samples | Median seconds | p95 seconds | Maximum seconds |
| --- | ---: | ---: | ---: | ---: |
| Sources | 25 | 0.175 | 1.309 | 2.789 |
| Subjects | 25 | 0.235 | 0.278 | 0.294 |
| Gallery | 50 | 0.262 | 1.489 | 3.711 |

These empirical p95 values use nearest-rank percentiles. All three are below two seconds in this sample. A contemporaneous database snapshot showed eleven idle connections and zero waiting locks. Retain the earlier gallery outliers and this sample's maxima when assessing tail latency.

The first handover preserved 34 completed profiles and selected 901 unfinished profiles. Six more profiles completed acquisition/handoff during the initial pilot. After every task in that execution became terminal, the final downloader release resumed **895 unfinished profiles**, preserving the original backfill ID and generation-checked checkpoints. Current execution: `face-user-backfill-perf2-20260919-pvk9n`. Acquisition completion and successful video processing remain separate metrics. Existing archive runs were reused through scoped identity lookups.

The pilot identified two issues that were corrected: repeated ingestion cold starts left processing slots idle, and enrollment needed a second pooled connection for independent cleanup receipts. The dispatcher now stays available while pending work exists, up to a 14-minute runtime window. The two affected matching operations were requeued after all corresponding GPU execution tasks were confirmed terminal. Both recovered operations completed successfully. Database checks confirmed all 49 selected groups in the first run and both groups in the second have their examples and representative records; a subsequent normal run also completed with its example and representative intact.

Validation evidence:

- Core suite: 168 passed, 22 browser cases skipped, no failures (`final-tests.xml`).
- Focused Chromium queue-progress test: passed, including continued polling after acquisition handoff and visible submission failures.
- Incremental downloader/validation checks: 18 passed.
- Dispatcher/admission/recovery checks after the runtime reuse change: 15 passed.
- Pooled enrollment regression: passed for both successful publication and independent cleanup receipts surviving an enrollment rollback.
- Terraform validation passed. Final Terraform formatting and validation passed.

### Rebuilding acquisition

Build the console image using `Dockerfile.console` with the scaffold directory as context, then build acquisition from the repository root:

```sh
docker build -f data-batch-gcp-scaffold/Dockerfile.acquisition \
  --build-arg CONSOLE_IMAGE=REGISTRY/console@sha256:DIGEST \
  -t REGISTRY/acquisition-batch:RELEASE .
```

The acquisition image includes the matching worker modules and downloader package. Incremental manifest indexes are passed into the downloader, and manifest records retain separate download/validation and archive-upload timing fields. Checkpoints retain profile discovery duration. Queue timestamps and run-operation timestamps provide admission/fetch/processing timing; structured processing logs report detection and matching/enrollment durations.

A repeat of the 100-request mixed check after processing recovery returned HTTP 200 throughout: sources median/p95 0.171/0.203 seconds, subjects 0.205/0.249 seconds, and gallery 0.230/0.294 seconds. This provides a second workload observation alongside the earlier table.

The final acquisition image built successfully from the tracked Dockerfile; a container smoke check verified worker imports and the incremental downloader interface. Live manifests confirmed the new timing fields: sampled download/validation medians ranged from 4.5 to 7.1 seconds/video, upload medians from 0.27 to 0.47 seconds, and profile discovery from 3.7 seconds for small profiles to about 20–22 seconds for profiles containing over 100 videos. Two acquisition tasks were active together with no acquisition failures in that sample. Both temporary performance diagnostic jobs were deleted after their executions completed.

## Requirement verification map

| Plan area | Implementation and evidence |
| --- | --- |
| A1 | Saved before/software-only/resize HTTP timings; source SQL plan profile; repeated mixed traffic checks. |
| A2 | Bounded subject-member loading, page-first browsing where semantics permit, optional predicate removal and batched ranking metadata. Real PostgreSQL tests cover counts, sorting, pagination, exclusions and source boundaries. Existing indexes measured adequate for the profiled loader. |
| A3 | Per-process pools, finite checkout timeout, rollback/session reset and reconnect tests; threaded console and bounded connection settings; live restart and gallery/API checks. Pooled enrollment success/rollback regression covers its independent receipt transaction. |
| A4 | Completed `db-g1-small` resize; before/software-only/resize comparisons; Monitoring memory/CPU samples; prior tier configuration retained. |
| B1 | Scoped archive index/lookups, incremental manifest state across acquisition and downloader, batched status reads, durable queue/checkpoint receipts, stage timing logs/manifest fields. Regression checks and live timing samples passed. |
| B2 | Queue migration/grants, atomic claims, leases, idempotency, retry/recovery sweep, interactive reservation and pending/review limits. Real PostgreSQL fault/replay/capacity tests; browser completion/polling test; live dispatch and recovery. |
| B3 | Two concurrent profiles; existing provider limits retained. Every task was terminal before checkpoint ownership transfer; immutable input checksums and generation preconditions preserved. Only unfinished profiles were resumed. |
| B4 | Two batch slots plus one interactive reservation; shared enrollment lock retained. The final pilot and low GPU utilization support retaining this concurrency. |
| B5 | Stage timing now separates detection and matching/enrollment. The measured short matching stage supports keeping the combined execution; model reuse is the next optional experiment. |
| Acceptance | Platform latency, enrollment preservation and queue accounting have live/test evidence. Final throughput, minimum-aware compute estimates, completion scenarios and successful dispatcher rollover are recorded below. |

The normal connection allocation is up to twelve console connections, six GPU connections across three active processing jobs, two acquisition connections and one dispatcher connection. Short recovery/maintenance jobs and administrative tools need additional headroom. Pools open connections lazily and allow no overflow.

## Pilot results and capacity decision

The first long-lived dispatcher, `face-ingest-drain-mcrk5`, exited successfully at 09:32:04 UTC after its planned runtime. A replacement acquired the lease at approximately 09:32:33 UTC and continued dispatch. The lease remained exclusive and the observed batch admission count remained at most two. This verifies the runtime handover as well as the isolated lease tests.

The comparison below uses the same ongoing retained-video backfill, with different videos in each time window. It is an operational pilot, not a paired-video benchmark. The post-change processing sample is small and includes a mix of face-bearing and no-face videos. Mean input size was 3.0 MB before and 4.7 MB afterward.

| Metric | Before: 07:30–08:00 UTC | Final pilot: 09:22–09:32 UTC |
| --- | ---: | ---: |
| Successful new video processing | 17 | 8 |
| Successful videos/hour | 34 | 48 |
| Processing failures in window | 0 | 0 |
| Mean recorded admission-to-completion duration | 281 seconds | 125 seconds |
| Acquisition records completed in window | 7 | 149 |
| Acquisition rate while queue has room | 14 videos/hour | 894 videos/hour |
| Acquisition failures in window | 0 | 0 |
| Estimated import compute per 1,000 processed videos | $29–47 | $27–35 |

Processing throughput was approximately 41% higher in this sample. Acquisition can now run ahead and hand off durable work; its burst rate will settle to processing throughput when the 1,000-item queue fills. Download completion is distinct from successful video processing.

Cost estimates combine Cloud Monitoring's `container/billable_instance_time` with public resource rates and an allowance for the one-minute minimum on completed task attempts. For each job class, the allowance ranges from the greater of measured seconds or 60 seconds per completed attempt, through measured seconds plus that minimum allowance. Completed-attempt counts and metric intervals have boundary effects; the pilot also includes recovery overhead and acquisition of future work. These are planning ranges, not invoice reconciliation or a proven percentage cost reduction. Raw metric-derived cost alone would understate short-job estimates. Sources: [Cloud Run pricing](https://cloud.google.com/run/pricing) and [billable-time metric](https://docs.cloud.google.com/monitoring/api/metrics_gcp_p_z#run).

Retain the current **two batch slots plus one interactive reservation** and **two acquisition profiles**. During the pilot, database CPU averaged 11.2% and memory 55.9%, compared with 21.6% and 100% in the earlier window. Average read operations fell from about 7,264 to 575 per sampled minute. These compare the combined software/database changes, rather than isolating the effect of RAM alone. The `db-g1-small` tier supplies useful memory headroom within the reviewed budget.

GPU utilization averaged approximately 2.6% across 21 reported samples, with about 1.4% GPU-memory utilization. Normal sampled detection took 5.5–12.6 seconds and matching/enrollment about 0.9–1.0 seconds; the larger 49-group case took roughly 61 seconds for detection and 22 seconds for recovered matching/enrollment. Provisioning and minimum billing are material at this scale. Keep matching/enrollment in the same execution. The next optional experiment should test processing multiple videos per model initialization; a separate CPU completion job would add startup overhead to the short ordinary matching stage. The current pilot supports retaining the selected concurrency and budget.

## Completion and remaining-cost scenarios

At the inventory snapshot, **44 profiles had been discovered, containing 728 selected videos**; **43 profiles had finished acquisition/handoff**. The observed mean was 16.5 videos/profile, median three, and range 1–114. Scoped archive/run checks found **226 discovered videos still awaiting acquisition or processing**, with no acquisition or terminal processing failures in that inventory. There were **891 undiscovered profiles**. Counts continue changing as the import runs.

Use 30–60 processed videos/hour for planning around the 48/hour pilot, conditional on a similar video mix. The following scenarios vary the average size of the undiscovered profiles; they are assumptions, not statistical confidence bounds.

| Average videos per undiscovered profile | Estimated videos remaining | Time at 30–60/hour | Remaining import compute |
| --- | ---: | ---: | ---: |
| 3 | 2,899 | 2–4 days | $78–102 |
| Observed mean: 16.5 | 14,969 | 10–21 days | $403–527 |
| 50 | 44,776 | 31–62 days | $1,207–1,577 |

The observed-mean scenario is approximately **13 days at the pilot rate**. These compute estimates cover acquisition, ingestion and GPU execution using the pilot allowance; database, storage, networking, builds, logs and taxes are additional. Database plus the existing 10 GiB SSD is approximately **$27.25/month**, or **$17.89/month above the original tier**, before backup/usage charges. Source: [Cloud SQL pricing](https://cloud.google.com/sql/pricing).

## Completion audit

Implementation and required pilot verification are complete. Source/subject/gallery latency meets the measured acceptance targets; matching/enrollment preserves selected examples and representative records; queue/admission/retry behavior has both integration and live evidence; checkpoint handover, two-profile acquisition, dispatcher rollover, stage measurements, cost comparison and remaining-work scenarios are recorded above. Optional higher-concurrency and model-reuse experiments were evaluated and remain later capacity decisions.

The original bulk import continues under the final release. Completing all remaining profiles is ongoing operation, separate from completing this performance implementation. Runtime failures remain visible in batch status, and the two-minute reconciliation schedule recovers durable queued work. Preserve queue/checkpoint records if changing consumers or rolling back application images.

Stage accounting also measured a median 900-second durable-queue admission wait (eight pilot videos; maximum 1,085 seconds), which includes the rollout backlog and recovery interval. This wait is separate from the 125-second mean after run creation. Two normal executions showed 102–117 seconds between completed fetch and entry into detection, covering job invocation/provisioning/startup. Fetch itself took roughly 0.5–0.8 seconds in those samples. These measurements identify scheduling/startup and backlog as the main remaining latency components for small videos. The GPU Dockerfile also defaults its pool to two connections, matching the verified Terraform/runtime setting.

Final read-only source-scan probes covered the three sources previously still above 40 subjects. All returned HTTP 200 in under one second, within the existing 20-second interactive budget; no merges were applied. At the 09:44 UTC status check, the final continuation had completed 14 additional profile handoffs (54 of 935 overall), with 248 pending queue entries, 29 submitted runs already successful and two batch runs admitted. The queue had no submission errors. Import continues from its existing execution.
