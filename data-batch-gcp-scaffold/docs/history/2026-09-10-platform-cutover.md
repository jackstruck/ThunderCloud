# Platform cutover — 2026-09-10

Schema 024 and the consolidated console/CPU/GPU release are deployed; both schedulers
are enabled. Production migration preserved all fourteen data projections, with zero
invariant violations. The live archive run passed acquisition, CUDA processing,
versioned results, retry and exact-generation cleanup. The expiry regression and
missing archive/gallery permissions found during acceptance were fixed and verified.
All 22,984 subjects, 43,670 examples and 8,682 active gallery images are preserved.

The retired GPU-drain job, obsolete alerts and temporary migration runtime access
are removed. Recovery artifacts were exported and then removed at the user’s request on
2026-09-10, together with the remaining three storage/access resources. Terraform reports no drift. The user confirmed browser sign-in and the requested UI changes on 2026-09-11,
completing acceptance.

See [the cutover results](../platform-cutover-review.md) for current image pins,
verification details, protected evidence locations, and recovery instructions.
