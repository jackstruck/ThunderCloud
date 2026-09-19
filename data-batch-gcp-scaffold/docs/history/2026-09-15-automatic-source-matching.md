# Automatic matching during new-source enrollment

The user accepted cosine 0.5 after reviewing source matching and requested that new
sources automatically create and merge matching groups, retaining all examples.

`InteractiveRepository.complete_matching` now performs one source-local complete-linkage
pass after enrollment recalculates embeddings and before the successful run commits.
The existing merge validator and mutation code run on the enrollment cursor under the
same advisory lock. Automatic merge events use actor `automatic-source-matching` and
stable operation IDs derived from the run, algorithm, threshold, and group members.
The source metadata retains an `automatic_matching` report with outcomes and exclusions.

All examples, representative images and source attribution survive merges. Search-only
runs and duplicate-source reuse skip automatic matching. Multi-source subjects, model
mismatches, excluded subjects, dismissed pairs, identity conflicts, and existing scan
or group-size limits retain the operator workflow's restrictions. Unexpected failures
roll back enrollment and merges together; exceeded planning bounds retain enrollment
and mark the source for manual review. Existing sources are not rescanned.

The new `source_merges_auto_threshold` Terraform variable controls
`FACE_SOURCE_MERGES_AUTO_THRESHOLD` on `face-interactive-gpu`; null/empty disables it.
Production is configured for 0.5 independently of the operator application gate.

The release image layers only the three changed worker modules on the currently deployed
GPU image `sha256:d1d4768347123f6a7889093c0cc9b45d6730d833c07fdf1417a7875842f67282`.
The model and CUDA dependencies are inherited unchanged. Image module imports passed;
shared subject management and run repository hashes match the locally tested files.
Release image: `us-central1-docker.pkg.dev/teak-banner-dome/face-batch-worker/worker@sha256:77f71e63f03cd14bc2aa57859a6c3ba67b23c913020c79b4770d157baef7c90e`.

Validation includes real PostgreSQL enrollment for both enrollment policies, complete-linkage
chain protection, cross-source isolation, preserving all examples and gallery images,
atomic failure/retry, duplicate-video reuse, identity conflicts, dismissed pairs and
planning limits. The broader browser run encountered existing stale UI selectors (including
the removed “Multiple sources” checkbox); no UI files changed in this release.

Backend acceptance: 237 passed, 20 browser cases skipped. The four automatic-matching
cases passed again after adding explicit duplicate-reuse assertions. Ruff and Terraform
validation passed. The deployed job reports Ready, the expected image digest, and
`FACE_SOURCE_MERGES_AUTO_THRESHOLD=0.5`; all other runtime settings are unchanged.
No production source was submitted or historical source merged during verification.
