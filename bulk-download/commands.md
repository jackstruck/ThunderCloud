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


# Continue Codex convo
codex resume 01a059d9-0b0d-7592-a137-15ba8d60bf8e
