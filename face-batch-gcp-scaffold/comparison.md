# Bulk download and face-batch comparison

## Summary

`bulk-download` is an acquisition and archival tool. `face-batch-gcp-scaffold` is the
managed face-processing, search, enrollment, and gallery system. The original downloader
assembled the encrypted `videos/` corpus, but it does not contain representative-gallery
backfill logic.

| Capability | `bulk-download` | Current `face-batch` |
| --- | --- | --- |
| HeyLink → JustPaste → Luluvid discovery | Yes | HeyLink intentionally rejected |
| Multiple Luluvid links per JustPaste page | Yes | Requires exactly one Luluvid source |
| Static Luluvid/JWPlayer extraction | Yes | Yes |
| HLS playlist/segment download and MP4 remux | Yes | No |
| MP4, WebM, and QuickTime acquisition | Yes | Interactive ingestion accepts MP4 only |
| Large-media streaming to disk | Yes, up to 10 GiB | Interactive retrieval buffers media in memory and defaults to 250 MiB |
| Content-hash deduplication | Local manifest-based | Database-backed retained-source deduplication |
| Existing-GCS recovery | Yes | Managed retries and generation-pinned records instead |
| CSEK-encrypted archival upload | Yes | Yes |
| Connection-time SSRF protection | DNS checked before the HTTP client resolves again | Validated IP pinned to the TLS connection |
| Durable distributed queue | No | Cloud SQL leases and Cloud Run jobs |
| Face detection, tracking, and quality selection | No | SCRFD, ByteTrack, and quality selection |
| Embeddings and subject matching | No | AdaFace and pgvector |
| Subject/gallery database | No | Yes |
| Representative crop generation | No | Yes, through `worker/gallery_backfill.py` |
| Authenticated API/UI | No | Yes |
| Retention and enrollment policy | No | Yes |
| Cleanup and lifecycle tracking | Local partial-file cleanup | Generation-pinned managed cleanup |

## Capabilities unique to the original downloader

The portions of `bulk-download` that remain useful are:

- Mature historical source discovery.
- Multiple-media extraction from one JustPaste page.
- HLS handling and lossless MP4 remuxing.
- Streaming large downloads without holding the complete video in memory.
- WebM and QuickTime support.
- Deterministic Luluvid-derived UUIDs and the historical append-only manifest.
- Content-hash duplicate detection during initial acquisition.
- Recovery of objects present in GCS but missing from the local manifest.

These features explain how the existing corpus was assembled. They do not create or
backfill subject galleries.

## Current representative-gallery backfill

`worker/gallery_backfill.py` starts from existing `face_track` records and:

1. Finds subjects with fewer than five active representative faces.
2. Selects a high-quality track and its generation-pinned source video.
3. Downloads and verifies the entire CSEK-encrypted video.
4. Re-runs detection, tracking, and embedding.
5. Associates the regenerated track with the existing track using time overlap and
   embedding similarity.
6. Uploads a representative JPEG under `subject-gallery/`.
7. Publishes up to five active representatives per subject.

Regeneration is necessary because the original face-processing rollout retained track
embeddings and metadata, but not centrally usable crop objects.

The live database currently has 22 active representative faces for 22 subjects. This is
consistent with the documented bounded sample using `FACE_BACKFILL_LIMIT=25`; it is not
evidence of a corpus-wide backfill. Run
`17dcb7d2-c2b4-4749-ab20-d90441f33702` returned ten candidates, none of which had an
active representative face.

## Backfill gaps

The current implementation is appropriate for a bounded canary but is not yet a
reliable full-corpus drain:

- It has a numeric limit but no durable backfill queue or per-source checkpoint.
- The limit applies to candidate tracks rather than distinct subjects or sources.
- Failed or ambiguous associations are not durably marked, so later executions can
  repeatedly select the same unresolvable tracks.
- It downloads and reprocesses a whole video even when only one short track is needed.
- Its downloaded-source cache exists only for one execution.
- It reports aggregate failure counts without recording the affected source and reason.
- It does not explicitly reconcile inactive objects left by an interrupted execution.
- Regeneration assumes the source is MP4.
- It considers legacy `face_track` lineage only. Subjects created from Phase Two
  `submission_enrollment` records are not eligible.
- Merge/split correction retires representatives but does not automatically enqueue
  their regeneration.

## Current bulk-rollout status

The principal historical rollout processed 1,858 of 1,859 items successfully. Its Cloud
Run execution completed successfully, but the durable rollout is correctly marked
failed because this source is dead-lettered as `INVALID_INPUT`:

```text
gs://teak-banner-dome-bulk-videos/videos/60887bf2-42f4-5b96-8613-23894f8bf8a1.mp4
generation 1788224908631738
```

One separate termination-test work item remains in `retry`, but that source succeeded in
the main rollout and does not represent another unprocessed video.

## Recommendation

Keep `bulk-download` as historical acquisition tooling. Extend `face-batch` for the
gallery backfill rather than adapting the old downloader.

The lean next improvement is a durable gallery-backfill drain with:

- one work item per subject/source pair;
- `succeeded`, `skipped`, `retry`, and `dead_letter` outcomes;
- recorded association failures;
- retry-safe representative publication;
- support for both `face_track` and Phase Two enrollment lineage; and
- a completion report proving gallery coverage for all eligible subjects.

This fills the actual missing capability while continuing to use the existing encrypted,
generation-pinned source corpus.
