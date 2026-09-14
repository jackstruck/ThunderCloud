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

The downloader does not automatically enqueue face processing. Use
[archive submission](../face-batch-gcp-scaffold/OPERATIONS.md#submit-archive-objects-through-the-common-run-framework)
to prepare and submit durable receipts for selected new sources. Reuse a receipt
on retry and check existing submissions before processing historical objects.

Keep configuration, keys, credentials, manifests, and temporary media out of Git.
The resolver uses static pages; unsupported or unavailable sources remain failures.

## Hotscope user downloads

Use the same config, CSEK, and GCS bucket as the existing downloader:

```bash
bulk-download discover --user USERNAME --max-videos-per-user 5
bulk-download run --user USER_A --user USER_B --max-videos-per-user 5
bulk-download run --users-file input/hotscope_users.txt --videos-file input/hotscope_videos.txt
```

Repeat `--user` or provide one username/profile URL per line in `--users-file`.
Select individual videos with repeated `--video-id ID` or a `--videos-file`
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
See [the acquisition design](../docs/design/hotscope-acquisition.md) for supported
formats, recovery behavior, and limitations.

HLS uses `IMAGEIO_FFMPEG_EXE` when set, then a system `ffmpeg`, then the bundled
imageio binary. Install system FFmpeg if the bundled binary is incompatible with
the host. The generated-media tests exercise remuxing, audio/video decoding, and
cleanup (`python -m unittest discover -s tests`).
