# Luluvid aliases and HLS ingestion — 2026-09-17

Runs 254e4e60-1eec-432d-a5c3-1259bb58f184 and
052d9300-dd8b-419e-9244-264179d89514 submitted luluvdo.com pages. The deployed
resolver treated that hostname as direct media, producing source_rejected when
HTML reached the media fetcher.

The resolver now accepts luluvdo.com and luluvdoo.com, including www, directly
and through JustPaste links. It resolves aliases through luluvid.com and
deduplicates equivalent links. Matching provider embed URLs supply playback
referers without executing JavaScript.

Luluvid/JustPaste HLS now uses the existing bounded MPEG-TS acquisition and MP4
validation pipeline. Every playlist, segment and redirect is restricted to the
resolved media hostname with connection-time public-address validation. Existing
byte, duration, segment-count and time limits, full decode and duration checks
remain in force. Hotscope retains its fixed CDN allowlist.

Validation: all 45 focused resolver, ingestion, HLS and secure-fetch tests passed;
Ruff passed on the three changed worker modules. Both reported links completed
full acquisition and validation locally and in the release container.

Deployed console and ingestion image:
sha256:b7e35722e80d006598ed6ad4b4f273ff0362ba3c874d3153dc18869e2e6f4753.
Console revision face-console-00048-lr7 serves 100% of traffic. Local Terraform
console_image matches deployment and retains the previous digest for rollback.

Production Cloud Run smoke execution face-luluvdo-smoke-20260917-swrjg succeeded:
both original links fully acquired and validated (11,827,030 and 6,018,546 bytes).
No archive or subject records were created by the smoke test.

At the user's request, both original failed runs were enabled for retry after
checking their IDs, URLs, failed fetch state and absence of acquired objects.
The standard retry API requeued both at 21:50 UTC with retain_and_enroll. Both
returned fetching; existing manual face-selection settings were preserved.
Temporary verification/preparation jobs and deployment credentials were removed.
