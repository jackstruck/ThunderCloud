# Source library entries without subjects

Completed runs register a source even if detection produces no groups or matching
produces no candidates. Source registration runs inside the completion transaction,
uses the same content lock as enrollment, and reuses an existing active source by
SHA-256. It records URL aliases without creating subjects or examples. Existing
archive references can be associated with retained-policy source records. A source
record does not imply that previously discarded temporary media was recovered.

Source browsing starts from source_asset and left joins enrolled examples, so a
source with zero subjects/examples is included. Deleted sources remain excluded.
The multiple-subject filter still excludes these sources. View Source always uses
/sources/<source_id>; the prior run-only overview is no longer its destination.
Historical successful runs lacking a source are registered by an idempotent,
per-run transactional backfill in worker/source_registry.py.

Validation: 36 focused unit checks, 9 isolated PostgreSQL/browser checks covering
empty detections under all policies, zero-count browsing, repeated backfill and
SHA reuse, duplicate enrollment, and navigation without candidates.

Additional subject-page layout correction: the open subject's top source actions
have two equal columns. Example View Run/Open Source actions remain on the same
row at 36px high, verified at 390px and 1280px viewport widths.
