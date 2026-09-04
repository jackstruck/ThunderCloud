# Browser Console Commands

Open the HeyLink page in your browser, complete any interactive challenge, open the browser developer console, and run one of the commands below.

## Copy links in the downloader's input format

```javascript
const links = [...new Set(
  [...document.querySelectorAll('a[href]')]
    .map(a => {
      try {
        return new URL(a.href, location.href);
      } catch {
        return null;
      }
    })
    .filter(u =>
      u && ['justpaste.it', 'www.justpaste.it'].includes(u.hostname.toLowerCase())
    )
    .map(u => {
      u.hash = '';
      u.search = '';
      return u.href;
    })
)];

const output = links.join('\n');
console.log(output);
copy(output);
```

This produces exactly the expected file contents: one clean JustPaste URL per line, without JSON brackets, quotes, commas, fragments, or tracking query parameters. The `copy(...)` helper is supported by Chromium-based browser developer consoles. If it is unavailable, copy the logged text manually.

## Add the links to the downloader

Paste the copied text directly into `input/justpaste_urls.txt`. For example:

```text
https://justpaste.it/example-one
https://justpaste.it/example-two
```

Because the seeded HeyLink URL currently returns an access challenge, comment it out in `input/heylink_urls.txt` by adding `#` at the beginning of its line when using the direct JustPaste input.

## Populate direct Luluvid input

Resolve every JustPaste page and append each direct Luluvid URL immediately:

```bash
python scripts/populate_luluvid_urls.py --config ./config.toml
```

Output is appended to `input/luluvid_urls.txt`, one URL per line. Completed source pages are checkpointed in `data/justpaste_resolution.jsonl`. If the command is interrupted or rate-limited, run the same command again to continue without duplicating links or reprocessing completed pages.

### Resume after rate limiting or interruption

Wait for the JustPaste rate limit to clear, then run:

```bash
cd /workspaces/ThunderCloud/bulk-download
source .venv/bin/activate
python scripts/populate_luluvid_urls.py --config ./config.toml --delay 1.0
```

The script reads `data/justpaste_resolution.jsonl`, skips completed JustPaste pages, and immediately appends each newly discovered URL to `input/luluvid_urls.txt`. It exits with status `75` when JustPaste responds with HTTP 429; rerun the same command later to continue.

Check saved progress with:

```bash
wc -l input/luluvid_urls.txt data/justpaste_resolution.jsonl
sort input/luluvid_urls.txt | uniq | wc -l
```

## Download and CSEK-upload direct Luluvid URLs

Test one item:

```bash
python scripts/run_luluvid_batch.py --config ./config.toml --limit 1
```

Run or resume the complete direct-Luluvid batch:

```bash
python scripts/run_luluvid_batch.py --config ./config.toml
```

Run two non-overlapping workers:

```bash
python scripts/run_luluvid_batch.py --config ./config.toml --partition front
python scripts/run_luluvid_batch.py --config ./config.toml --partition back --reverse
```

The two-worker mode uses a cross-process content lock for the final SHA-256 check and upload, preserving content deduplication across both halves.

The script refuses to run unless bucket soft-delete retention is zero. It resolves one Luluvid page at a time, downloads its HLS segments, remuxes them locally to a UID-named MP4, uploads with the configured CSEK, verifies the stored size, appends completion to `data/manifest.jsonl`, and deletes the local temporary video. Rerunning skips completed manifest entries and recovers objects already present in GCS. If a new Luluvid URL downloads to a SHA-256 hash already recorded in the manifest, it is recorded with status `duplicate` and a `duplicate_of` object reference; no second object is uploaded.

## Hand off new uploads to face processing

Completing the download command does not enqueue face processing. After all new URLs
have a terminal `complete` or `duplicate` manifest record, switch to the face-processing
project:

```bash
cd /workspaces/ThunderCloud/face-batch-gcp-scaffold
set -a
. ./.env
set +a
```

Do not create a second rollout while an existing rollout is `running`. Check and
reconcile the active rollout documented in `commands.md` in this directory before
continuing. Once no rollout is active, count the unique manifest items that do not yet
have a succeeded processing job:

```bash
python scripts/select_remaining.py \
  --manifest ../bulk-download/data/manifest.jsonl \
  --count-only
```

Review that count. Then create a new, private selection file using the exact reported
number; the command refuses to overwrite an existing file or proceed if the count
changed between review and creation:

```bash
python scripts/select_remaining.py \
  --manifest ../bulk-download/data/manifest.jsonl \
  --output selections/justpaste-YYYYMMDD.txt \
  --confirm-count REVIEWED_COUNT
```

Follow the manifest enqueue command in `FUTURE_VIDEOS.md`, using that selection file,
a new stable request key, and the documented immutable image and model versions. Then:

```bash
face-cloud-run status --rollout-id ROLLOUT_ID
face-cloud-run start --rollout-id ROLLOUT_ID --tasks 1 --parallelism 1
face-cloud-run reconcile --rollout-id ROLLOUT_ID
```

The start command returns while processing continues remotely. Run reconciliation only
after the rollout is terminal. Completion requires every requested item to succeed and
zero retryable, dead-letter, active, missing-commit, provenance-mismatch, or lingering
staging counts. Do not silently omit failed acquisition records; resolve or explicitly
record them before declaring the added JustPaste set complete.


# Continue Codex convo
Bulk Download: codex resume 01a059d9-0b0d-7592-a137-15ba8d60bf8e
Face Batch: codex resume 01a05d3f-5aa4-7a71-97d5-29d7999a1ca9
Face Probe and Planning: codex resume 01a063ae-b120-7323-8818-601721b8bae1
Phase One: codex resume 01a06444-3cfd-7743-aca4-6975a19c4668
Phase Two and Three (Up to enroll behavior): codex resume 01a06716-ec74-7ce2-b093-44a16b6e9bda 
Backfill Stage 0: codex resume 01a06783-9a32-7471-9c41-14731348b4bb