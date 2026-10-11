# Within-source bulk matching and merging

Historical design proposal, preserved from an ignored local note on 2026-10-11.
Its proposed interfaces, rollout steps, and statements about work that did not yet
exist describe the design stage; they are not current operating instructions.
Operator-approved group application was later enabled, and the automatic
new-source threshold subsequently changed from 0.5 to 0.4.

For implemented behavior and operator instructions, see
[source-merges.md](../source-merges.md).

## Objective

Identify separate groups of matching subjects within each video source and apply selected groups in bulk. Support both the console and a local operator command. Never merge across source boundaries in this workflow.

This design is independent of acquisition. It operates on already enrolled
examples and requires neither new video downloads nor GPU processing.

## Current behavior and reusable components

- `/sources/{source_id}` lists subjects, source-specific thumbnails, and their time spans. Operators can select subjects, choose a surviving identity, and merge directly.
- `SubjectManagement.potential_matches` ranks up to ten subjects across the library using cosine similarity of canonical embeddings. It does not establish an identity threshold or restrict candidates to a source.
- `SubjectManagement.bulk_combine` merges entire subjects, checks versions and embedding-model compatibility, and supports 2–50 subjects per operation.
- The existing transaction lock, operation IDs, merge events, and separation workflow provide concurrency protection, replay handling, and recovery.

The existing global top-ten list is unsuitable as the candidate set: relevant within-source subjects may be displaced by matches elsewhere. Showing subjects on a source page also does not limit the effects of a whole-subject merge.

## Strict source boundary

Version one includes only current, unmerged subjects whose examples belong to exactly one source, equal to the source being scanned. Check this from current example membership, not an origin label or an old processing record.

Skip subjects spanning multiple sources and show that reason. Do not split them automatically or discard their other memberships. A later workflow could explicitly separate source-specific examples before matching, but that is outside this design.

Apply the same boundary checks again inside the write transaction. A subject gaining another source between proposal and application makes that group stale; no members of that group are merged.

## Matching and grouping

1. Load eligible subjects and their current embeddings, versions, example counts, thumbnails, and source time spans.
2. Partition by embedding-model version. Skip missing, invalid, or zero-sample embeddings.
3. Compute pairwise cosine similarities within each partition. Reuse stored canonical embeddings for these single-source subjects; do not compare embeddings produced by different models.
4. Build deterministic, disjoint groups using complete-linkage clustering: combine two groups only when every cross-group pair meets the configured threshold. Break ties by stable subject IDs.
5. Respect current dismissed pairs as hard exclusions from a group. Leave singletons unchanged.
6. Return proposed groups with the minimum pairwise similarity, representative previews, and member time spans. Similarity is a score, not an identity probability.

Do not group by connected components alone: A matching B and B matching C does not establish that A matches C. Time spans help human review; overlap alone is not a reliable prohibition because a video can contain cuts, mirrors, or repeated imagery.

The threshold must be calibrated with reviewed examples from the deployed model. No universal numerical default or accuracy guarantee is assumed. Initial rollout generates proposals only. Unattended application remains disabled until reviewed outcomes establish an acceptable false-merge rate.

A suggested survivor is the member with the most examples, with subject ID as the deterministic tie-breaker. Operators can select a different survivor and see its identity details before applying. Conflicting populated identity details flag the group for explicit review.

## Console workflow

On a source page, add **Find matching groups**. Show one compact panel per proposed group, containing:

- Member thumbnails, IDs or names, source time spans, and minimum similarity.
- A checkbox to include the group and an inline **Keep subject** selector.
- A way to exclude a member or dismiss an incorrect pair; revalidate any edited group.
- Any exclusions or identity conflicts requiring attention.

**Merge selected groups** applies the selected groups directly, without another confirmation modal. Keep ordinary manual selection available as a separate operation so it is clear which groups will be merged.

On Sources, allow selecting multiple sources and scanning each independently. Show progress and results per source; never use a cross-source candidate pool. Do not silently expand an operation to the whole library.

Report merged, skipped, stale, and failed groups separately. Keep failed groups available for review or rescan. Existing **Separate a previous merge** remains available on resulting subjects, subject to its current membership and recent-history limits; it is not an unconditional undo guarantee.

## Shared service and proposed interfaces

Add a planner next to `worker/subject_management.py`, reusing database access and the current similarity calculation. Expose source-scoped proposal and apply endpoints through the existing authenticated console. Proposed routes:

- `POST /api/sources/{source_id}/merge-proposals`: read-only computation with validated matching configuration.
- `POST /api/sources/{source_id}/merge-proposals/apply`: apply explicitly selected groups and survivor choices.

These are design names, not existing endpoints. Update OpenAPI during implementation.

A proposal contains a format/algorithm version, source ID, model and threshold, member IDs and expected versions, survivor choice, and scores. The apply service must recheck membership, model compatibility, current dismissals, identity conflicts, and pairwise eligibility; a caller-supplied score or plan file is not trusted evidence.

Refactor the existing merge implementation to allow strict-scope validation and mutation under the same transaction and lock. Do not perform a scope check in one connection followed by an unchecked merge in another.

Each group is atomic and has a persistent operation ID. Multiple groups are not one global transaction: completed groups remain completed if a later group fails. Reusing an operation ID with the identical payload returns its result; a different payload conflicts. A rescan creates a new plan, not a new identity for an uncertain previous operation.

Validate only the current group's members when applying it so completing one disjoint group does not invalidate every remaining group in the same source. Newly enrolled subjects are considered on the next scan, never silently added to an approved group.

Groups exceeding the existing 50-subject limit require manual handling in version one. Do not silently split a large group into sequential merges to evade that limit.

## Local operation and execution limits

Provide a thin CLI over the same authenticated service. Proposed commands are `source-merges plan` and `source-merges apply`, with explicit source IDs or an input file. Planning writes a private, versioned JSON plan; application records each group's original operation ID and outcome for resumption. These commands do not exist yet.

Do not write directly to subject tables from a local script. Reuse the console's authorization and merge semantics. Process sources sequentially initially, with a documented per-source comparison/work limit. Pairwise comparison is quadratic; sources exceeding the request-time or comparison budget should be reported as needing a bounded background scan, not partially scanned without notice.

No new service, database, bucket, or GPU job is required initially. If measured volumes exceed synchronous limits, add resumable execution using existing worker infrastructure as a separately reviewed extension.

## Validation and rollout

Acceptance requires tests demonstrating:

- Same-source true matches form distinct groups; unrelated subjects remain separate.
- Cross-source lookalikes and multi-source members are never included or mutated.
- A–B–C chains with a weak A–C pair do not collapse into one group.
- Model mismatches, missing embeddings, dismissed pairs, and conflicting identities are handled explicitly.
- New source membership, version changes, or concurrent merges reject an entire affected group before mutation.
- Replay after an interrupted response produces no duplicate merge, and partial batch completion resumes correctly.
- Source attribution remains intact and eligible members can be separated using the existing recovery workflow.
- Local and UI application produce identical results; mobile group review is usable.

Roll out in three stages: read-only proposals on a small reviewed source set; operator-approved bulk groups; optional unattended application only after calibration. Record false merges and missed matches separately, and retain the configured algorithm and threshold with each operation.
