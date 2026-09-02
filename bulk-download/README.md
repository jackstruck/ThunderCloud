# Bulk Video Download to Google Cloud Storage

## Fixed implementation specification

Build a local Python command-line program that follows this exact chain for content the operator is authorized to access and download:

```text
heylink.me page -> justpaste.it page -> luluvid.com page -> video URL
                                                      -> local temp file
                                                      -> CSEK-encrypted GCS object
```

The fixed Google Cloud settings are:

- Project: `teak-banner-dome`
- Bucket: `teak-banner-dome-bulk-videos`
- Object prefix: `videos/`
- Object name: `videos/<uid>.<extension>`

GCS bucket names are globally unique. The provisioning command must fail clearly if `teak-banner-dome-bulk-videos` is already owned by another project; in that case, update the bucket value in `config.toml` before running the downloader.

This first version is intentionally local and synchronous. It will not use SQLite, Playwright, a background service, or a test suite.

## Decisions

- Language: Python 3.12+
- Package manager: `pip`
- HTTP: `httpx`
- HTML parser: `beautifulsoup4` with `html.parser`
- GCS client: `google-cloud-storage`
- Configuration: one local `config.toml` file
- Input: one local newline-delimited text file
- Progress record: append-only local JSON Lines manifest
- Concurrency: none; process one item at a time
- UID: UUIDv5 derived from the canonical Luluvid page URL
- Browser automation: none
- Authentication: Google Application Default Credentials
- Retry policy: three attempts for transient failures

UUIDv5 makes naming deterministic without a database: the same canonical Luluvid page always produces the same UID. Use `uuid.uuid5(uuid.NAMESPACE_URL, canonical_luluvid_url)`. Different Luluvid URLs produce different names, and rerunning the same input targets the same object.

## Repository layout

```text
bulk-download/
├── README.md
├── requirements.txt
├── config.toml
├── config.toml.example
├── .gitignore
├── input/
│   ├── heylink_urls.txt
│   └── justpaste_urls.txt
├── data/
│   ├── manifest.jsonl
│   └── tmp/
└── src/
    └── bulk_download/
        ├── __init__.py
        ├── __main__.py
        ├── cli.py
        ├── config.py
        ├── urls.py
        ├── fetch.py
        ├── resolvers.py
        ├── download.py
        ├── storage.py
        └── manifest.py
```

## Local configuration

`config.toml` is the sole application configuration file:

```toml
[gcp]
project = "teak-banner-dome"
bucket = "teak-banner-dome-bulk-videos"
prefix = "videos"
csek_file = "/absolute/path/outside-this-repository/gcs-csek.base64"

[input]
heylink_file = "input/heylink_urls.txt"
justpaste_file = "input/justpaste_urls.txt"

[local]
temp_dir = "data/tmp"
manifest_file = "data/manifest.jsonl"

[http]
user_agent = "CloudDownload/1.0"
connect_timeout_seconds = 10
read_timeout_seconds = 60
max_redirects = 5
attempts = 3
backoff_seconds = 2
max_html_bytes = 5242880
max_video_bytes = 10737418240

[download]
chunk_bytes = 8388608
allowed_content_types = ["video/mp4", "video/webm", "video/quicktime"]
```

Rules:

- Resolve relative paths against the directory containing `config.toml`, not the current shell directory.
- Require every setting; do not silently invent defaults.
- Reject unknown top-level configuration sections and unknown keys.
- Require an absolute `csek_file` path.
- The CSEK file contains one standard Base64-encoded AES-256 key. Decode with strict Base64 validation and require exactly 32 bytes.
- Read the key once at startup into memory. Never print it, hash it into logs, persist it in the manifest, or put it in object metadata.
- Set the key file to owner-read-only permissions, such as `0600`.
- Exclude `config.toml`, `*.base64`, `data/`, credentials, and `.env*` in `.gitignore`. Commit only `config.toml.example` with a placeholder key path.

## Installation and GCP setup

`requirements.txt` contains exact compatible-version pins for:

```text
beautifulsoup4
google-cloud-storage
httpx
imageio-ffmpeg
```

Local setup:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
gcloud auth application-default login
gcloud config set project teak-banner-dome
gcloud storage buckets create gs://teak-banner-dome-bulk-videos \
  --project=teak-banner-dome \
  --location=US \
  --uniform-bucket-level-access
```

The checked-in `config.toml` is ready except for `gcp.csek_file`. Replace its placeholder with the absolute path to your local Base64 CSEK file before running `doctor` or `run`. The `discover` command does not read the key or contact GCS, so it can be used first.

Bucket creation is a one-time operator action. The downloader itself does not create, modify, or delete buckets. The authenticated principal needs permission to create and inspect objects in the bucket.

## Command interface

Implement exactly these commands:

```bash
# Check config syntax, key format, ADC, and bucket accessibility.
python -m bulk_download doctor --config ./config.toml

# Resolve the page chain and print results, without downloading or uploading.
python -m bulk_download discover --config ./config.toml

# Resolve, download, and upload all input items sequentially.
python -m bulk_download run --config ./config.toml
```

All commands return exit code `0` only if every requested item succeeds. Configuration errors return `2`; partial or complete item failures return `1`.

`doctor` performs read-only checks. It does not upload a probe object.

## Deterministic URL rules

Every URL goes through one canonicalization function before filtering or UID generation:

1. Parse with `urllib.parse.urlsplit`.
2. Require `https`.
3. Lowercase the hostname and remove a trailing dot.
4. Reject embedded credentials and non-default ports.
5. Remove the fragment.
6. Preserve path and query because they may identify the page.
7. Normalize an empty path to `/`.
8. Rebuild with `urlunsplit`.

Host allowlists are exact:

- HeyLink stage: `heylink.me` and `www.heylink.me`
- JustPaste stage: `justpaste.it` and `www.justpaste.it`
- Luluvid stage: `luluvid.com` and `www.luluvid.com`

Do not use substring host matching. `luluvid.com.example.org` must be rejected.

For every request and redirect, resolve the hostname and reject loopback, private, link-local, multicast, reserved, and cloud metadata addresses. Revalidate the final URL after redirects. This prevents extracted links from turning the downloader into an SSRF client.

## Exact processing algorithm

### 1. Load input

Read `input.heylink_file` and `input.justpaste_file` as UTF-8. Each file contains one URL per line, not a JSON array. Trim whitespace, ignore empty lines and lines beginning with `#`, canonicalize each URL, enforce the host appropriate to its file, and remove duplicate canonical URLs while preserving first-seen order. Either file may be empty, but both files must exist.

HeyLink inputs follow the complete resolver chain. JustPaste inputs skip the HeyLink stage and begin directly at step 4. Results from both files are deduplicated by canonical Luluvid URL.

### 2. Fetch HTML

For each page, make an HTTP `GET` with the configured user agent and timeouts. Follow at most five redirects. Accept only a `2xx` response with a media type of `text/html` or `application/xhtml+xml`. Stop reading if HTML exceeds `max_html_bytes`.

Retry only connection failures, timeouts, HTTP `429`, and HTTP `500`, `502`, `503`, or `504`. Make at most three total attempts. Wait 2 seconds before the second attempt and 4 seconds before the third, unless a valid `Retry-After` requires a longer wait. Other `4xx` responses fail immediately.

### 3. Resolve HeyLink to JustPaste

Parse the final HeyLink HTML with Beautiful Soup. Inspect `<a href>` attributes in document order, resolve relative links against the final response URL, canonicalize them, and keep only allowed JustPaste hosts. Remove duplicates while preserving order.

If no JustPaste links appear in static HTML, record `no_justpaste_link` and stop that input. There is no browser fallback.

### 4. Resolve JustPaste to Luluvid

For every JustPaste URL, repeat the same static HTML fetch and anchor extraction. Keep only canonical links on allowed Luluvid hosts and deduplicate them in first-seen order.

If none appear, record `no_luluvid_link` and continue to the next JustPaste page.

### 5. Resolve the video URL

Fetch each Luluvid page as static HTML. Select the first valid candidate in this fixed priority order:

1. `<video src="...">`
2. `<video><source src="...">` in document order
3. `<meta property="og:video:secure_url" content="...">`
4. `<meta property="og:video" content="...">`
5. The HTTPS `.m3u8` value assigned to `sources[].file` in Luluvid's standard packed JWPlayer setup

Resolve relative values against the final Luluvid response URL and require HTTPS. The packed-player fallback decodes only the recognized Dean Edwards packer structure already present in the static HTML; it never executes page JavaScript. Accept only an HTTPS `.m3u8` value in the JWPlayer source field. Do not call undocumented APIs or bypass authentication, DRM, CAPTCHA, expiring-link controls, or other access restrictions. If static HTML exposes no candidate, record `no_static_video_url`.

For HLS input, fetch and validate the master and media playlists with the normal HTTP client, reject encrypted, byte-range, and fragmented-MP4 playlists, then download MPEG-TS segments sequentially with the same bounded client. Concatenate those transport-stream segments locally and use the FFmpeg binary bundled by `imageio-ffmpeg` only for a lossless local MP4 remux. Enforce the configured maximum size throughout and delete partial output on failure. No browser, `yt-dlp`, or system FFmpeg installation is required.

The media host may differ from `luluvid.com`, but it must pass the same scheme, redirect, DNS, and IP safety checks.

### 6. Assign the UID and extension

Compute:

```python
uid = str(uuid.uuid5(uuid.NAMESPACE_URL, canonical_luluvid_url))
```

The extension comes only from the video response's normalized `Content-Type`:

```text
video/mp4       -> .mp4
video/webm      -> .webm
video/quicktime -> .mov
```

Any other content type fails with `unsupported_video_type`. The object name is exactly `<prefix>/<uid><extension>`, for example:

```text
videos/61f1047c-7e2c-5d16-b4fc-9022f2f52f16.mp4
```

### 7. Skip already completed items

At startup, read `manifest.jsonl` and collect the latest `complete` record for each canonical Luluvid URL. Skip those items.

Before downloading any item not marked complete, construct its deterministic object name and query GCS using the CSEK. If that object exists, download that exact generation once with the CSEK to calculate SHA-256 and verify its byte length, then record an ingestion-complete `complete` entry with `recovered_from_gcs: true`, `sha256`, `bytes`, `generation`, and `content_type`. Delete the temporary recovery file after verification. If the object does not exist, proceed. A local manifest is a convenience record, not the source of truth.

### 8. Download locally

Stream the video to `data/tmp/<uid>.part` in 8 MiB chunks. Open the destination in exclusive-create mode so an unexpected existing partial file is not overwritten. Validate the final response status and allowed video content type before writing bytes.

While streaming:

- Abort if bytes exceed `max_video_bytes`.
- Compute SHA-256.
- Count bytes.
- Flush and `fsync` before upload.

Reject HTML and empty responses. On a retryable download failure, remove only that item's `.part` file and retry the media request using the standard attempt policy.

### 9. Upload with CSEK

Use the raw decoded 32-byte CSEK on the blob:

```python
from google.cloud import storage

client = storage.Client(project=config.gcp.project)
bucket = client.bucket(config.gcp.bucket)
blob = bucket.blob(object_name, encryption_key=raw_csek)
blob.upload_from_filename(
    temp_path,
    content_type=content_type,
    if_generation_match=0,
    timeout=300,
)
```

`if_generation_match=0` prevents overwriting an existing object. Use the library's resumable upload behavior for these file uploads. After upload, reload the blob with the same CSEK and require its size to match the downloaded byte count. Record the returned generation.

GCS performs CSEK encryption during upload. The same key must be supplied for later reads, relevant metadata operations, rewrites, and downloads. Cloud Storage does not retain a recoverable copy of the key.

### 10. Record and clean up

Append one JSON object per outcome to `manifest.jsonl`. Open the manifest in append mode, write one compact JSON object followed by a newline, flush, and `fsync` it. Never rewrite previous lines.

A success record contains:

```json
{"timestamp":"2026-08-31T12:00:00Z","status":"complete","heylink_url":"https://heylink.me/example/","justpaste_url":"https://justpaste.it/example","luluvid_url":"https://luluvid.com/example","uid":"61f1047c-7e2c-5d16-b4fc-9022f2f52f16","object":"gs://teak-banner-dome-bulk-videos/videos/61f1047c-7e2c-5d16-b4fc-9022f2f52f16.mp4","generation":123456789,"content_type":"video/mp4","bytes":1234567,"sha256":"hex-digest"}
```

A failure record includes `timestamp`, `status: "failed"`, the available source URLs, a stable `error_code`, and a redacted message. Do not record the media URL because it may contain short-lived credentials. Never record cookies, headers, query parameters from media URLs, local credentials, or CSEK material.

Delete `data/tmp/<uid>.part` only after GCS verification and the success manifest write both complete. On a terminal failure, delete the partial file. At startup, report stale `.part` files and require the operator to remove them; do not guess which job owns an unexpected file.

## Logging

Write human-readable progress to stderr and reserve stdout for `discover` results. Each log entry includes the stage, UID when assigned, and stable result code. Redact URL query strings and never log response headers or bodies.

Stable error codes:

```text
invalid_config
invalid_csek
invalid_input_url
unsafe_url
access_challenge
http_failure
html_too_large
no_justpaste_link
no_luluvid_link
no_static_video_url
unsupported_video_type
video_too_large
download_failure
object_exists
upload_failure
verification_failure
manifest_failure
```

## Implementation order

1. Create the package layout, pinned requirements, `.gitignore`, example config, and empty input file.
2. Implement strict TOML loading and CSEK validation.
3. Implement URL canonicalization, host filtering, DNS/IP safety checks, and bounded HTTP fetching.
4. Implement the three static-HTML resolvers with the exact selection order above.
5. Implement UUIDv5 naming and content-type extension mapping.
6. Implement streamed local download, SHA-256, size limits, and cleanup.
7. Implement GCS CSEK existence checks, upload, and post-upload verification.
8. Implement append-only manifest recovery and the three CLI commands.
9. Run `doctor`, then `discover`, then a single authorized end-to-end item before adding the remaining input URLs.

## Completion criteria

- `doctor` validates the local config, CSEK, ADC, and access to `teak-banner-dome-bulk-videos` without changing cloud state.
- `discover` deterministically reports the static HeyLink -> JustPaste -> Luluvid -> video chain.
- `run` processes one item at a time and uploads only allowed video types.
- The same canonical Luluvid URL always produces the same UUIDv5 filename.
- Uploads use CSEK and cannot overwrite an existing generation.
- Successful objects are size-verified with the same CSEK before local plaintext is deleted.
- Reruns skip completed objects using the manifest and GCS checks.
- If different Luluvid URLs produce identical SHA-256 content, only the first completed object is uploaded. Later matches are recorded as `duplicate` with a `duplicate_of` object reference.
- No key material, credentials, signed media URLs, or cookies enter logs, the manifest, GCS metadata, or source control.

Uploading completes acquisition but does not start face processing. After adding new
JustPaste links, follow **Hand off new uploads to face processing** in `commands.md` to
select manifest items not yet represented by a succeeded Cloud SQL processing job,
enqueue them, start the Cloud Run GPU Job, and reconcile the result.

## References

- [Google Cloud: Customer-supplied encryption keys](https://docs.cloud.google.com/storage/docs/encryption/customer-supplied-keys)
- [Google Cloud: Use customer-supplied encryption keys](https://docs.cloud.google.com/storage/docs/encryption/using-customer-supplied-keys)
- [Google Cloud Python sample: Upload an object using CSEK](https://docs.cloud.google.com/storage/docs/samples/storage-upload-encrypted-file)
