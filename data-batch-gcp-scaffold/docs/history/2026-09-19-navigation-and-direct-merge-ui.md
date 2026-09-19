# Navigation rows and direct potential-match merge — 2026-09-19

Results now place View Source / Open Source in equal halves at the top. Enrolled
and candidate cards use three equal navigation columns: View Subject, View Source,
and Open Source. External source links have an SVG external-link icon and retain
HTTPS filtering, new-tab behavior and noopener/noreferrer. Multiple source links
remain accessible within their columns.

Subject cards say View Subject, with subject names in merge-checkbox labels.
Source-list checkboxes use source IDs; source links are buttons with subject counts
on internal navigation, and external navigation uses Open Source. Removed requested
source-list and merge-review helper text and the source self-link above matching
groups. Matching scan button/input heights match. Selection and matching controls
fill their rows.

Potential matches has Find potential matches, Show dismissed, and Merge selected
matches in one equal-width three-column row. Each candidate has full-width Merge
and Dismiss buttons. Candidate Merge directly calls the existing version-checked
combine endpoint, keeping the currently viewed subject. It skips the confirmation
modal and preserves mutate()'s stable operation IDs for uncertain retries. On
success, subject data and potential matches refresh; conflicts expose Refresh matches.
Other bulk-merge and manual correction flows retain their existing behavior.

Validation: JS syntax and diff checks passed. Read-only Playwright navigation checks
used live data at 390px and 1280px. Direct-merge failure/retry requests were intercepted
locally to verify operation ID reuse and no modal, without mutating production.
The focused real-PostgreSQL browser dismissal/restore/direct-merge integration case
passed with the current subject retained and all examples preserved.

Console-only deployment:
`console@sha256:ee03ec65be9e2957d8dea58243debb45cfbc76fb077b7b1fa9ae8bbdfa76b168`
under `us-central1-docker.pkg.dev/teak-banner-dome/face-batch-worker/`.
Revision `face-console-00051-7bq` serves 100% of traffic. Only the three UI asset
files were layered onto the prior console image. Ingestion/GPU/backfill jobs are
unchanged. Local Terraform console_image reflects this release. Private evidence
and screenshots are under `.local/ui-navigation/`.
Live verification confirmed all three served assets match local files, equal-width
rows at both viewport sizes, matched scan/input heights, external icons, removed
labels, and the intercepted direct-merge retry behavior. Deployment credentials
were removed and the temporary PostgreSQL container was stopped.


## Run-button wrapping correction — 2026-09-19

Removed percentage-height stretching from navigation buttons. Run result navigation
now places individual View Subject / View Source / Open Source buttons into a
responsive grid (up to three columns) rather than stretching nested source columns.
Buttons remain single-line, 44px high; additional sources wrap to subsequent rows
and narrow cards reduce the column count. Checked overflow and button bounds at
320, 390, 768 and 1280px with a 12-source result.

Latest console image: `us-central1-docker.pkg.dev/teak-banner-dome/face-batch-worker/console@sha256:e6eac8aa82a515823d2a9d7a3f2954345aafadcb8e4e94d39b47a77211b20d56`.
Only app.js/styles.css changed in this correction; processing jobs are unchanged.
Private deployment and browser evidence: `.local/ui-wrap/`.
