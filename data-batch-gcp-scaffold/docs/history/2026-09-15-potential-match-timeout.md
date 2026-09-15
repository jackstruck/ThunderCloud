# Potential-match timeout — 2026-09-15

Subject efab0ba9-c4cc-4566-aaca-f657413f89d2 returned a 504 while finding
potential merges. The ranking query alone exceeded a 25-second diagnostic limit.
EXPLAIN showed a hash join, sequential scan of approximately 43,000 eligible
subjects, and full sort; the existing HNSW similarity index was not used.

The origin vector/model/version are now loaded once in the same repeatable-read
transaction and passed as constant values. Direct ascending cosine distance with
a UUID tiebreaker enables the HNSW index and incremental sort. Normal suggestions
use ef_search=40 and strict iterative scanning past exclusions. The dismissed view
retains exact ranking to retrieve dismissals outside the nearest-neighbor window.
No schema, infrastructure sizing, or timeout changes were needed.

The complete production-backed check returned ten candidates, gallery previews,
and source URLs in 6.72 seconds, with candidate ranking taking 1.74 seconds. The
existing potential-match dismissal/version/ownership regression passed. A new real
PostgreSQL regression verified index eligibility, ten eligible results past 60
closer incompatible subjects, UUID tie order, and a distant dismissed subject.
The local test database was capped at half a CPU and removed after testing.

Cloud Build 7e97f5bd-ceef-4570-8d4e-67b7dd07a481 produced:
us-central1-docker.pkg.dev/teak-banner-dome/face-batch-worker/console@sha256:0e084578cc74f0759ab5b8c06fe6f7daf41e6a88911d81c3af7dd1f24eeb39ad

Deployed console revision face-console-00039-ssv was verified ready with IAP enabled
and 100% latest-revision traffic. Terraform's console image and registry rollback
pins were updated. No worker jobs were launched or changed by this fix.
