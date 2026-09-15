# Browse filters and privacy view — 2026-09-14

Console revision `face-console-00037-994` is ready with 100% traffic and IAP enabled.
Image: `us-central1-docker.pkg.dev/teak-banner-dome/face-batch-worker/console@sha256:c3f4fa0d0a2ce80940df7b7f014742790e5655aeede54f7ad36efdccf82c4566`.
The previous console digest remains pinned for rollback.

Subjects gained most-examples and newest sorts, named/unnamed filtering, and a
source-count dropdown with all/single/multiple choices. Sources gained
most-subjects, most-examples, newest, and exact normalized origin-host filtering.
Existing filters and old bookmarks remain supported. Count/date cursors include
sort, numeric metric, and UUID so tied values paginate consistently; controls
reset cursors when filters change and retain choices in the URL. No database
migration was needed.

The privacy eye toggle sits left of the theme button, greys out all gallery
images via the root document state, applies to dynamic images, and remembers its
state in localStorage. A synchronous head script applies the saved preference
before galleries render. The header has compact spacing on small screens.

Validation used an isolated PostgreSQL container: existing non-browser subject
management tests and new tied-pagination/filter tests passed. API forwarding,
privacy asset serving, eleven Node DOM/import tests, JavaScript syntax, diff
checks, and container HTTP smoke checks passed. Read-only live queries verified
successive filtered pages had no overlap. Cloud Run readiness, digest, traffic,
and IAP were verified. The isolated database was removed; no Chromium was used.
