# Local video acquisition

This Python CLI resolves HeyLink → JustPaste → Luluvid links, downloads videos
locally, and uploads immutable CSEK-encrypted objects under
`gs://teak-banner-dome-bulk-videos/videos/`. Acquisition runs sequentially.

Use [Browser link collection](commands.md) to add direct JustPaste inputs.

## Setup

Requires Python 3.12 or newer:

```bash
cd /workspaces/ThunderCloud/bulk-download
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

Use the existing private `config.toml` for this deployment. On a new machine,
start from `config.toml.example` and supply the existing bucket configuration,
Application Default Credentials, and absolute path to the existing CSEK file.
Do not replace an existing configuration or generate a replacement encryption key.
Relative input and temporary paths resolve against the configuration directory.

## Commands

```bash
# Read-only configuration, key, credentials, and bucket checks.
python -m bulk_download doctor --config ./config.toml

# Discover media from configured HeyLink/JustPaste inputs without uploading.
python -m bulk_download discover --config ./config.toml

# Acquire from configured HeyLink/JustPaste inputs.
python -m bulk_download run --config ./config.toml
```

For the existing checkpointed backfill, use the two scripts in the linked guide
instead: `scripts/populate_luluvid_urls.py` and `scripts/run_luluvid_batch.py`.

## Resume state and processing

Preserve the input files, `data/justpaste_resolution.jsonl`, and append-only
`data/manifest.jsonl`. The direct-Luluvid batch skips complete/duplicate URLs,
uses deterministic object names, recovers existing objects where possible, and
avoids duplicate-content uploads. Uploads cannot overwrite an existing generation.
Successful records include generation, SHA-256, byte count, and content type.

The downloader does not automatically enqueue data processing. Use
[archive submission](../data-batch-gcp-scaffold/OPERATIONS.md#submit-archive-objects-through-the-common-run-framework)
to prepare and submit durable receipts for selected new sources. Reuse a receipt
on retry and check existing submissions before processing historical objects.

Keep configuration, keys, credentials, manifests, and temporary media out of Git.
The resolver uses static pages; unsupported or unavailable sources remain failures.

## Hotscope downloads

Use the same config, CSEK, and GCS bucket as the existing downloader:

```bash
# From the bulk-download directory: download only the listed videos, without usernames.
bulk-download discover --videos-file input/hotscope_videos.txt
bulk-download run --videos-file input/hotscope_videos.txt
bulk-download run --video-url https://hotscope.tv/video/VIDEO_ID

bulk-download discover --user USERNAME --max-videos-per-user 5
bulk-download run --user USER_A --user USER_B --max-videos-per-user 5
bulk-download run --users-file input/hotscope_users.txt --videos-file input/hotscope_videos.txt
```

With only `--videos-file`, `--video-url`, or `--video-id`, each specific video is resolved directly; no profile listings are fetched. Duplicate IDs are collapsed in file order, completed entries are skipped on reruns, and failed links are recorded while the remaining links continue. An empty or invalid list fails before storage access.

Repeat `--user` or provide one username/profile URL per line in `--users-file`.
When users are supplied, filter their listings with repeated `--video-id ID` or a `--videos-file`
containing IDs or Hotscope video-page URLs. Blank lines and `#` comments are
ignored. Without a selection/count limit, all videos listed for the supplied
users are selected. `--max-pages` defaults to 100 per user; unfinished pagination
and selected IDs not found are reported as failures. Count limits use provider
order and include previously completed videos, making repeated runs stable.
Hotscope commands do not require the legacy input files to exist.

Full HLS videos are downloaded, validated, and uploaded with the configured CSEK;
reruns skip completed pages and deduplicate identical content. GCS upload is the
end of this workflow. Run one writer per manifest/temp directory. The process
must stay running; local scratch media and manifests are not encrypted by CSEK.

HLS uses `IMAGEIO_FFMPEG_EXE` when set, then a system `ffmpeg`, then the bundled
imageio binary. Install system FFmpeg if the bundled binary is incompatible with
the host. The generated-media tests exercise remuxing, audio/video decoding, and
cleanup (`python -m unittest discover -s tests`).

Existing HLS archives can be reused across FFmpeg remux versions even when MP4
container bytes differ. Reuse requires an integrity-checked download of the exact
stored generation and identical compressed-packet SHA-256 fingerprints including
codec metadata, stream layout and timestamps. The manifest records the archived
file's SHA-256 and size. Different media is rejected, and archives are never
overwritten by this reconciliation.

HLS verification still decodes the full video and checks playlist duration. If
FFmpeg rejects reserved H.264 color tags, validation uses a temporary stream-copy
with unspecified color tags; the archived file is unchanged. Verification failures
include a stage and diagnostic message in the manifest.

### Export user directory links in the browser

Open `https://hotscope.tv/users`, then paste
[`scripts/export-search-links.js`](scripts/export-search-links.js) into the browser
console. It starts at `/users`, follows directory pagination such as
`/users?page=2`, and downloads deduplicated profile URLs (one per line) as
`user-links.txt`. The same script still exports video URLs on a search results page.

The existing controls apply to both modes:
`window.videoLinkExportOptions = { maxPages: 1000, pauseMs: 700 }` before running;
`window.videoLinkExport.stop()` to stop; `window.videoLinkExport.urls` to inspect
collected links. Failed, stopped, or page-limited exports are marked partial and
use `user-links-partial.txt`. Completion means all discovered pagination links
were exhausted. This collects URLs only; it does not submit or download videos.
