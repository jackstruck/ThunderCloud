# Future handoff inventory

The private list is [data/handoff-candidates.json](data/handoff-candidates.json).
It currently contains the completed `izno` test download. It is an inventory for
review; creating or editing it does not submit work. The downloader does not
currently maintain this separate list automatically.

Each `items` entry has four parts:

| Part | Meaning |
| --- | --- |
| `candidate_id` | Stable acquisition UUID for the video. |
| `origin` | Provider, provider video ID, and uploader username. |
| `acquisition` | Completed download status, timestamp, and originating manifest. |
| `source` | Exact archived file: bucket, object name, immutable generation, SHA-256, byte count, media type, and original page URL. |
| `handoff` | Review state and future submission choices; initially unselected, with no principal, policies, receipt, or run. |

The `source` object uses the field names expected by the existing archive
submission adapter. Bucket plus object name locates a file; generation identifies
the exact stored version. SHA-256 and byte count identify its content. The page URL
preserves attribution. The CSEK itself never belongs in this list.

`handoff.status = "not_requested"` describes this inventory's state only; it is
not evidence that no other workflow has ever submitted that source. Before a
future handoff, existing submissions would need checking by exact source identity.
The principal and policies remain undecided. A real preparation step would create
a separate durable receipt and idempotency key; an accepted submission would supply
a run ID. This inventory is not a submission receipt and is not accepted directly
by `face-submit`.

To extend the inventory, add one entry per completed immutable source using the
exact manifest metadata. Deduplicate by bucket, object name, and generation. A
manifest `duplicate` row refers to an existing source; resolve its canonical
complete record rather than treating it as a new object. Keep the acquisition
manifest unchanged and retain the private inventory under the Git-ignored `data/`
directory.
