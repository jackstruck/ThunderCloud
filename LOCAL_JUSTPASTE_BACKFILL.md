# Local backfill of outstanding JustPaste videos

Run these commands from a local terminal in this checkout. They resolve outstanding
JustPaste pages, download their Luluvid videos sequentially, and upload immutable,
CSEK-encrypted sources to `gs://teak-banner-dome-bulk-videos/videos/`.
Face processing is a separate handoff described at the end.

## Current starting point

Read from the local input, checkpoint, and manifest files on 2026-09-11:

| Local queue | Count |
| --- | ---: |
| JustPaste input pages | 333 |
| JustPaste pages without a completed resolution checkpoint | 98 |
| Already discovered, unique Luluvid URLs | 2,335 |
| Discovered Luluvid URLs without a complete or duplicate manifest record | 312 |

These are local acquisition counts, not live website availability or outstanding
face-processing counts. Resolving the 98 pages can add more Luluvid URLs. The 312
includes failed attempts and URLs without a terminal success record. Recount before
and after running; do not use the older 95–98 video estimate.

## 1. Prepare the terminal

```bash
cd /workspaces/ThunderCloud/bulk-download
source .venv/bin/activate
python -m bulk_download doctor --config ./config.toml
df -h .
```

If the environment is missing, create it with Python 3.12 or newer:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

Use the existing private `config.toml`, CSEK file, and Application Default
Credentials. If doctor reports expired or missing authentication, run
`gcloud auth application-default login`, then rerun doctor. Do not recreate the
existing bucket or encryption key. Configuration paths are relative to the config
file, except the CSEK path, which must be absolute.

Check free space on the filesystem containing the configured `local.temp_dir`.
The current per-video limit is 10 GiB, while the codespace had only about 4.3 GB
free after cleanup. HLS remuxing can temporarily hold both transport-stream and
MP4 files. For that limit, use a temporary directory on a volume with more than
20 GiB free plus headroom, or deliberately lower `http.max_video_bytes` to fit the
available space (larger videos will fail and remain outstanding). Run one worker.

Keep these existing files: they are the downloader's resume and deduplication state,
not optional audit exports:

- `input/justpaste_urls.txt`
- `input/luluvid_urls.txt`
- `data/justpaste_resolution.jsonl`
- `data/manifest.jsonl`

Do not truncate them or start with an empty manifest. No new audit report is needed.

## 2. Count outstanding work

Run this read-only snippet from `bulk-download/` whenever you need current counts:

```bash
python - <<'PY'
import sys
from pathlib import Path

sys.path.insert(0, 'scripts')
from populate_luluvid_urls import completed_pages
from bulk_download.config import load_config
from bulk_download.pipeline import load_inputs
from bulk_download.manifest import completed_urls

config = load_config('config.toml')
pages = load_inputs(config.justpaste_file, 'justpaste')
resolved = completed_pages(Path('data/justpaste_resolution.jsonl'))
urls = load_inputs(config.luluvid_file, 'luluvid')
done = completed_urls(config.manifest_file)
print(f'JustPaste: {len(pages)} total, {len(set(pages) - resolved)} unresolved')
print(f'Luluvid: {len(urls)} total, {len(set(urls) - done)} outstanding')
PY
```

## 3. Resolve the remaining JustPaste pages

```bash
python scripts/populate_luluvid_urls.py --config ./config.toml --delay 1.0
```

This reads the direct JustPaste input, skips completed pages, appends newly found
links to `input/luluvid_urls.txt`, and checkpoints each completed page immediately.
It does not require the HeyLink page to be accessible.

Exit code `75` means rate limiting or a page returning no Luluvid links. Wait before
rerunning the same command; a persistent no-links page needs inspection and may
not be throttling. Exit `1` means other page failures remain. Ctrl+C saves completed
progress; rerun to resume. Do not delete checkpoints to retry unfinished pages.
You can download already discovered URLs even if some pages remain unresolved.

## 4. Download and upload outstanding videos

Once temporary disk capacity is suitable, run:

```bash
python scripts/run_luluvid_batch.py --config ./config.toml --delay 1.0
```

For a deliberately bounded first session, add `--limit 1`; it attempts one pending
URL, which is not necessarily one successful upload. The full command resumes
without that limit. Keep a single foreground worker; no additional batch test is
required.

The script skips URLs with any `complete` or `duplicate` record, retries other
URLs, verifies existing objects when recoverable, and avoids uploading content
whose SHA-256 already has a completed manifest entry. Successful uploads record
exact generation, SHA-256, byte count, and content type. Temporary downloaded files
are removed after handling. A `duplicate` points to an existing source object.

The batch currently requires bucket soft-delete retention to be zero and refuses
otherwise. If that check fails, inspect the configuration with the current
operations guide rather than changing bucket policy as part of a retry.

Exit `0` means all attempted items succeeded; `1` means item failures remain; `2`
can indicate the bucket-policy refusal. Rerun the same command after addressing
failures. Ctrl+C stops local acquisition while preserving recorded completions.
If interrupted by a hard process kill, inspect leftover files in the configured
temporary directory after confirming no downloader is running. Remove only the
stale partial files for the stopped attempt before retrying.

## 5. Check completion or identify remaining failures

Rerun the count snippet. Acquisition is complete when both counts are zero.
If URLs are unavailable or unsupported, report them as unresolved instead of
marking them complete. This read-only snippet prints the remaining Luluvid URLs
and their latest recorded errors:

```bash
python - <<'PY'
import json
from bulk_download.config import load_config
from bulk_download.pipeline import load_inputs
from bulk_download.manifest import completed_urls

config = load_config('config.toml')
done = completed_urls(config.manifest_file)
latest = {}
if config.manifest_file.exists():
    for line in config.manifest_file.read_text().splitlines():
        if line.strip():
            row = json.loads(line)
            if row.get('luluvid_url'):
                latest[row['luluvid_url']] = row
for url in load_inputs(config.luluvid_file, 'luluvid'):
    if url not in done:
        print(url, latest.get(url, {}).get('error_code', 'not attempted'))
PY
```

## 6. Hand completed uploads to face processing, if required

Downloading does not enqueue processing. Use the current
[archive submission commands](face-batch-gcp-scaffold/OPERATIONS.md#submit-archive-objects-through-the-common-run-framework)
and [future-video runbook](face-batch-gcp-scaffold/FUTURE_VIDEOS.md).
For each selected, not-already-submitted source, prepare a private `face-submit`
receipt using its exact completed manifest metadata; include `--page-url` with the
Luluvid attribution. Choose the handling policy explicitly and use
`--selection-policy all_tracks` for unattended enrollment. Submit with an active-run
limit of three and reuse the same receipt on retry.

A duplicate record is not a new source to enroll: follow `duplicate_of` to the
original complete object and check its existing submission before doing anything.
Do not submit the entire historical manifest again. There is currently no automatic
handoff from this downloader to the common run framework.

Follow returned run IDs in **Check a run** at
<https://face-console-4nq5bomqgq-uc.a.run.app>. Processing completion is separate from
zero outstanding acquisition URLs. Use the receipt-based workflow linked above.
