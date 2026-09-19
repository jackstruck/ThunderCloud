# Platform and user/video import performance plan

Status: implemented and pilot-verified on 2026-09-19; bulk import continues. See [release evidence](history/2026-09-19-performance-implementation.md).

Prepared: 2026-09-19. Baseline observations were collected around 08:07 UTC that day; recheck configuration and workload before implementation.

## Recommendation and scope

Start with SQL and connection-handling improvements on the current database at **approximately $9.37/month including 10 GiB SSD**. Improve import scheduling alongside these changes. Upgrade the database if browsing latency or import throughput still needs more capacity.

The recommended next tier is **`db-g1-small`, with shared CPU and 1.7 GiB RAM**, retaining the current Enterprise edition and zonal availability. Estimated compute cost is **$25.55/month**, or **$27.25/month including the existing 10 GiB SSD**, plus backups and usage charges. This is approximately **$17.89/month above the current configuration** and provides about **2.8× the RAM**. Make this an optional capacity step after measuring the software improvements.

Improve gallery responsiveness through connection reuse, request handling and batched metadata queries, while retaining existing lazy loading and private-image access behavior.

Preserve matching thresholds, frame sampling, detection models, all examples, exclusions, identity safeguards, retention policies, exact-content deduplication and source attribution. Validate equivalent matching efficacy throughout the rollout.

## Baseline and evidence

| Area | Observed behavior |
| --- | --- |
| Database | `face-batch-pg`, `db-f1-micro`, 0.6 GiB RAM, Enterprise, zonal, 10 GiB SSD. Previous 30 minutes: reported memory utilization 100%, average CPU approximately 22%. |
| Console | 1 CPU / 512 MiB, two synchronous Gunicorn workers, Cloud Run concurrency 20, maximum three instances, 30-second request timeout. |
| Recent HTTP sample | Sources averaged 8.1 seconds; subjects 9.7 seconds; gallery images 5.9 seconds. Use these samples as the starting point for before-and-after comparisons. |
| Earlier source scan profile | Main aggregate query 36.7 seconds; full database load 40.3 seconds; connection setup 3.7 seconds; matching calculation 0.036 seconds. |
| User import | 935 profile tasks, parallelism one; 31 tasks had completed acquisition/handoff. Track video processing outcomes separately from handoff completion. |
| Import admission | Three active runs per principal; downloaded media was waiting for slots. |
| GPU execution | One L4, four vCPUs, 16 GiB per execution. Detection and subsequent matching/enrollment can run in the same execution. |
| Video bucket | US multi-region Standard storage, while compute is in `us-central1`. |

Focus on the slow SQL queries and connection overhead identified above; use before-and-after timings to track improvement.

Implementation references:

- [Source browsing and summaries](../worker/subject_management.py)
- [Source matching loader](../worker/source_merges.py)
- [Database connections and candidate lookup](../worker/db.py)
- [Gallery metadata/image flow](../worker/gallery.py)
- [Console runtime](../Dockerfile.console)
- [Acquisition and handoff loop](../worker/acquisition_batch.py)
- [Per-profile task wrapper](../worker/user_backfill.py)
- [Run admission](../worker/run_repository.py)
- [Detection/matching execution](../worker/interactive.py)
- [Enrollment transactions](../worker/interactive_repository.py)

## Plan A: platform responsiveness

### A1. Measure the improvement

1. Time source browsing, subject browsing, gallery images and a source scan during normal import activity.
2. Use the identified slow queries and connection overhead to start the fixes immediately; inspect query plans while implementing them.
3. Repeat the same requests after the changes and record the improvement.

Deliverable: a short before-and-after timing table collected during implementation.

### A2. Optimize SQL and preserve results

1. Refactor source matching to identify relevant subject IDs before aggregating their examples. Separate explicit-ID and source-discovery query paths where that improves planning.
2. Preserve checks across **all** examples of those subjects, including membership in other sources, disabled examples and model consistency.
3. Optimize source browsing by query mode. For simple ID/newest browsing, evaluate selecting the page before loading its counts. Count-based filters and sorts must still evaluate the full eligible population before pagination.
4. Remove unnecessary search predicates for empty searches. Evaluate an indexed normalized hostname for origin filtering if profiling supports it.
5. Evaluate composite indexes such as `subject_example(source_id, subject_id)` against actual plans. Existing source and subject indexes already exist; retain only additions with measured benefit.
6. Batch repeated candidate/example/source metadata lookups where the same information is currently fetched per result.

Validation: compare IDs, counts, pagination, ordering, zero-subject sources, exclusions and merge proposals against the existing implementation. Use concurrent index creation where appropriate and assess its production I/O impact.

### A3. Reuse connections and align HTTP concurrency

1. Add bounded database connection pooling with transaction rollback/reset on return, broken-connection handling and IAM-authenticated reconnects.
2. Initialize connectors and pools per worker process after Gunicorn forks. Give each concurrent request its own checked-out connection.
3. Set a total connection budget across console processes, console instances, ingestion and GPU jobs. Configure a finite pool overflow limit.
4. Trial **two Gunicorn workers with four threads each**, **1 CPU / 1 GiB**, and **Cloud Run concurrency eight**, initially retaining the maximum of three instances and scale-to-zero behavior.
5. Check thread safety and memory use of shared clients. Reduce concurrency if database waits rise; increase it only when measurements justify doing so.
6. Retain gallery authorization, active-image checks, encryption and existing response cache policy.

Validation: exercise mixed gallery/API traffic during imports, pool exhaustion, worker restart and database reconnection. Verify transaction isolation between requests and bounded connection-acquisition timeouts.

### A4. Trial the budget database upgrade

1. Compare the software-only results with the platform acceptance targets. If more database capacity is needed, resize to **`db-g1-small` with shared CPU and 1.7 GiB RAM**, retaining Enterprise edition, zonal availability, encryption, disk and backup settings.
2. Schedule the resize restart and coordinate import admission using the existing checkpoints.
3. Compare browsing latency and import throughput with the software-only results. Keep this tier if it meets the workload's needs; use those results to guide any later capacity decision.

Estimated database cost: **$27.25/month including the existing 10 GiB SSD**, plus backups and usage charges. Both the current and proposed tiers use shared CPU, so measure performance under normal concurrent browsing and imports.

Keep the previous tier configuration available for rollback, accounting for workload memory needs and the resize restart.

### Platform acceptance targets

- Ordinary source/subject browsing: p95 below two seconds under the agreed representative import load.
- Navigation/API requests meet their latency targets during concurrent gallery traffic.
- Source scans complete within the existing interactive 20-second budget for representative supported sources.
- Equivalent matching results, source counts, pagination and access controls.

Measure these targets during the pilot and use the results to determine rollout readiness. Retain the existing interactive scan workflow and limits.

## Plan B: faster user/video imports

### B1. Remove repeated work from acquisition

1. Replace the full archive-run-history query in each loop with indexed lookups scoped to the current archive identities and handling policy.
2. Maintain manifest-derived state incrementally within the task instead of repeatedly parsing all entries. Reconstruct it from the durable checkpoint after a restart.
3. Batch status reads and noncritical progress updates. Preserve the durable idempotency receipt before submission and generation preconditions on checkpoint writes.
4. Measure discovery, download/validation, archive upload, admission wait, fetch, GPU startup, detection and matching/enrollment separately.

Deliverable: less database and checkpoint traffic with equivalent run ownership, deduplication and retry behavior.

### B2. Separate downloaded work from processing admission

1. Introduce a durable per-video pending-work queue, using the existing PostgreSQL infrastructure.
2. Let acquisition finish after every discovered video has either a durable queue entry or a recorded acquisition failure. Expose acquisition completion separately from processing completion.
3. Dispatch queued items using atomic claims, leases, idempotent submissions and bounded retries. Add a recovery sweep for missed completion notifications or abandoned leases.
4. Trigger dispatch when processing capacity is released; use a bounded recovery poll and let acquisition containers exit after durable handoff.
5. Separate queued items and manual-selection waits from occupied processing capacity. Keep explicit limits on queued work, active processing and pending review to prevent uncontrolled growth.
6. Reserve an agreed share of processing capacity for interactive use and apply backpressure when the pending queue reaches its limit.

Validation: simulate duplicate delivery, crashes before/after submission, expired leases, full capacity and restart recovery. Each video must remain accounted for exactly once at the logical handoff level, even if execution retries occur.

### B3. Pilot bounded parallel acquisition

1. Begin with two concurrent profiles, then three if provider responses, memory, database load and queue depth remain healthy.
2. Keep provider-aware request limits and backoff. Adjust profile concurrency and within-video download concurrency in separate measured steps.
3. Replace the hardcoded three-run admission limit with one shared configuration/policy used by all submission paths, coordinating admission capacity with profile parallelism.
4. Migrate unfinished backfill work explicitly. Transfer checkpoint ownership explicitly and select unfinished profiles when creating a replacement execution.
5. Drain or checkpoint affected tasks before switching scheduling models, reconcile receipts, and resume only unfinished work.

### B4. Scale processing only after the pilot

1. Establish a baseline with up to three useful concurrent GPU executions, subject to available quota and interactive capacity.
2. Measure GPU utilization, admission wait, startup time, database time and enrollment lock waits. Job task parallelism and the number of separately launched executions are different controls.
3. Trial up to six concurrent GPU executions only if three are consistently utilized and the database remains responsive. Verify regional GPU quota before rollout.
4. Leave the shared enrollment/content lock in place initially. Faster SQL shortens its hold time; changing lock granularity requires a separate correctness design covering all enrollment, registration and merge paths.

Measure the throughput gain from each concurrency increase, accounting for shared locks, provider limits, startup overhead and database work.

### B5. Optional later experiment: reduce non-GPU work on GPU instances

Evaluate moving database-heavy matching/enrollment to CPU workers, or processing multiple videos per model initialization. First measure whether reduced GPU time outweighs extra job startup, queueing and checkpoint complexity. Proceed with an approach after demonstrating output equivalence and net cost benefit.

### Import acceptance targets

- Report successfully processed videos/hour, acquisition/handoff throughput and profile completion as separate metrics.
- Compare billed compute cost per successfully processed video on the same representative workload.
- Preserve all examples, thresholds, attribution, exact-content reuse and per-video failure visibility.
- Preserve complete work accounting and idempotent enrollment across retries/restarts.
- Keep Plan A's responsiveness targets while importing.
- Produce a revised completion estimate from discovered video counts and observed processing times, with a range for profiles not yet discovered.

## Estimated Google Cloud costs

Estimates use public USD list prices reviewed on 2026-09-19, `us-central1` compute, 730 hours/month, before committed-use discounts or free-tier credits. Recheck before implementation. Actual billing depends on measured usage; development effort is estimated separately.

### Database

| Configuration | RAM | Compute/month | With 10 GiB SSD | Increase over current |
| --- | ---: | ---: | ---: | ---: |
| Current `db-f1-micro`; software improvements first | 0.6 GiB | $7.67 | $9.37 | $0 |
| Recommended upgrade: `db-g1-small` | 1.7 GiB | $25.55 | $27.25 | $17.89 |

Compute calculation: `db-g1-small` is `$0.035 × 730 = $25.55/month`; current `db-f1-micro` is `$0.0105 × 730 = $7.665/month`. Existing 10 GiB SSD adds approximately $1.70/month. The increase is calculated before rounding: `$25.55 − $7.665 = $17.885`, approximately **$17.89/month**. These are shared-CPU, Enterprise, zonal rates; backups and usage charges are additional. Source: [Cloud SQL pricing](https://cloud.google.com/sql/pricing).

### Cloud Run

| Runtime | Estimated compute rate |
| --- | ---: |
| Acquisition: 1 CPU / 2 GiB | $0.0792/hour |
| Ingestion: 1 CPU / 1 GiB | $0.0720/hour |
| Current console: 1 CPU / 0.5 GiB | $0.0909/active instance-hour |
| Proposed console: 1 CPU / 1 GiB | $0.0954/active instance-hour |
| GPU execution: L4, 4 CPUs, 16 GiB, no zonal redundancy | $1.0465/hour |
| Three continuously busy GPU executions | $75.35/day |
| Six continuously busy GPU executions | $150.70/day |

Console examples assume request-based billing; active hours are summed across instances. At 100 active instance-hours/month, its proposed compute cost is approximately $9.54, versus $9.09 currently. Requests and network charges are additional.

GPU examples for 1,000 videos: five billed GPU minutes/video costs about $87; ten minutes costs $174; twenty minutes costs $349. Acquisition, ingestion, storage and database charges are extra. Jobs bill for instance lifetime, including waiting, with a one-minute minimum. Evaluate elapsed time and total billed worker-hours separately as parallelism increases. Source: [Cloud Run pricing](https://cloud.google.com/run/pricing).

### Storage and other charges

The current US multi-region Standard bucket costs approximately **$26 per 1,000 GiB/month**, plus operations and applicable transfers to regional compute. Include archive, retained, temporary, noncurrent and soft-deleted copies. Retain the current bucket location. Source: [Cloud Storage pricing](https://cloud.google.com/storage/pricing).

Budget for logging, builds, image storage, backups, network traffic and taxes in addition to these compute estimates. The SQL-backed queue uses the database capacity included above.

### Budget interpretation

- Software-first stage: retain the current database at approximately **$9.37/month including the existing SSD**.
- Recommended upgrade: approximately **$27.25/month including the existing SSD**, a fixed increase of approximately **$17.89/month**.
- Console memory increase: approximately **$0.0045 per active instance-hour**, assuming equal active time.
- Import cost is usage-dependent. A six-GPU configuration can spend approximately $151/day on GPU executions alone if continuously busy.
- Agree a daily import spend target and enforce concurrency/runtime limits in the dispatcher; billing alerts should supplement those controls.
- Replace the handoff's earlier $580 total-import extrapolation only after a representative pilot. Use discovered video counts, retry rates and billed processing times to calculate the full-import budget.

## Implementation sequence

1. **Quick performance check:** capture the slow-path timings and choose a representative set of requests/videos for comparison.
2. **Platform:** implement SQL and connection/thread improvements on the current database. Trial `db-g1-small` if the measured results call for more capacity.
3. **Acquisition:** optimize existing lookups and checkpoint work before changing scheduling.
4. **Queue pilot:** validate recovery and ownership transitions, then trial two concurrent profiles.
5. **Scale decision:** compare outputs, p95 API latency, successful videos/hour and cost/video before raising profile or GPU concurrency.
6. **Optional experiments:** evaluate CPU completion stages/model reuse only if profiling supports them.

Use isolated databases for tests that mutate or truncate data. Preserve previous images and configuration for rollback. Prefer additive schema changes; retain queue/checkpoint records across rollback and drain work deliberately before reverting a consumer. Preserve all pending work through rollback.

Current rollout settings: two concurrent acquisition profiles, two batch processing slots, one additional slot reserved for interactive submissions, a 1,000-item pending queue and 50 pending reviews per principal. GPU workers use two pooled database connections to support enrollment's independently committed gallery cleanup receipts. The dispatcher reuses a CPU job for up to 14 minutes while pending work waits for capacity.

At continuous utilization, two GPU executions cost about $50.23/day; two acquisition workers and one ingestion worker add about $5.53/day. Use **approximately $56/day for saturated import compute**, plus short reconciliation/cleanup invocations, database, storage and other usage charges. This is a capacity-based estimate; the pilot's measured billable time determines actual cost per processed video.

Pilot result: source/subject/gallery p95 was 0.203/0.249/0.294 seconds in the final mixed check. Video processing increased from 34/hour in the earlier sample to 48/hour in the ten-minute pilot. Estimated import compute is approximately $27–35 per 1,000 processed videos for the sampled workload, allowing for job billing minimums. The release evidence includes the sample limitations and remaining-import scenarios.
