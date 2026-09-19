# Profile submission and result navigation

The console link form accepts Hotscope `/user/name` paths and full profile URLs.
The user explicitly requested automatic submission of every video. The console
creates an encrypted, idempotent checkpoint and launches the existing cloud acquisition
job; discovery follows the same public profile pagination implementation as the local
downloader. The batch uses the selected handling policy and `all_tracks` selection.
A `/batches/<id>` page shows discovery/acquisition progress and current run links,
including preexisting archive runs. Processing persists when the browser closes.

The existing acquisition checkpoint IAM prefix and job invocation permissions cover
these operations. No new buckets, database schema or GPU job changes are required.
Concurrent launches use the checkpoint generation and execution owner; task retries
retain that owner. Source archive identity, handoff receipts and run quotas remain
unchanged. Discovery limits and acquisition failures are visible instead of silently
reporting a complete profile.

The text-file picker and its script inclusion were removed from the console. Local
list-based tooling remains available. Run results now carry independent source
attribution and outcome; empty candidate results and no-detection results still offer
View Source, using the library source when present and otherwise the submitted HTTPS
page. An upload with neither library source nor external attribution cannot offer a link.

Console image: `sha256:b98f1ff2444b6c362863d11ad42a2156a81de21e6257dbf09d468337685e2bf2`.
Acquisition image: `sha256:37ecb207573143e45a9aba13cfaa712ea35a8209f9d282c5db3aed945d7e1eef`.

Validation covers profile URL rejection, checkpoint idempotency and ownership, API
authentication/policy enforcement, full-list discovery (250 videos), incomplete
pagination rejection, browser submission/reload, removal of file import, and links
on both kinds of empty results. No real user profile is submitted as a smoke test.

Final console revision `face-console-00044-cbk` serves 100% of traffic. Live checks
confirmed matching deployed asset hashes, authenticated checkpoint access, independent
source attribution, removal of the file option, and mobile layout without overflow
or JavaScript errors. The upload input was constrained to its container on small screens.
Validation: 196 unit tests in the broad run, the added profile-discovery test in the
focused rerun, 52 database integration tests, and both new browser flows passed.
