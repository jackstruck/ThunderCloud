# Acquisition verification fixes — 2026-09-17

Reproduced the nine repeated verification failures in batch
15372e4f-5afa-5f63-97fb-df8f48d77708 using the deployed acquisition container.

Eight existing archives were created with FFmpeg 6.1, while cloud acquisition uses
FFmpeg 7.1.5. For the reproduced sample, both files decoded correctly but differed
by one container byte and checksum; packet/timestamp/codec fingerprints matched.
The ninth video (mhOgo) has reserved H.264 color metadata; FFmpeg 7 rejected it
before decoding, while FFmpeg 6 decoded it completely.

Existing HLS object reconciliation now verifies the exact archived generation,
its size/checksum/encryption/media type, and matches compressed packet hashes,
timestamps, codec metadata and stream layout against the fresh validated download.
It preserves the existing object and records its actual SHA-256/size for handoff.
Reserved color-tag validation uses a temporary H.264 metadata-only stream-copy,
then requires a successful complete decode and expected duration. Original media
bytes remain unchanged. Failures retain stage-specific messages in the manifest.

All nine were independently downloaded and verified with the fixed code inside
the deployed cloud container, without uploading during diagnosis. Eight archive
objects were confirmed reusable. All 33 downloader tests plus 3 subtests passed,
including rejection of changed media, wrong encryption keys and truncated videos.

Deployed acquisition image:
sha256:44419806666f4323e5409375c6791226765a12f16678e1c161b1ef63a510961a.
Only the nine failed entries were reset; earlier failures remain in retry history,
and the 141 successful submissions were preserved. Handling policy remains
retain_and_enroll. Updated the local Terraform image variable to match deployment.

Live result: execution face-acquisition-batch-m4zd8 acquired all nine successfully.
Batch checkpoint reports 150/150 acquired, zero acquisition failures, all 150
handed off or already processed. Eight retry records explicitly reuse existing
archive generations. At verification, 149 runs had succeeded and one was fetching.
