# Potential matches implementation — 2026-09-09

Implemented locally; no production migration or deployment was performed. The full
platform goal remains active, including processing consolidation and cloud cutover.

Migration 011 adds canonical unordered suggestion-dismissal pairs with existing
subject versions, actor/timestamps and explicit restoration metadata. Dismiss and
restore use the established subject mutation lock, ordered row locks, authenticated
actor, operation fingerprints and correction audit. Both subject versions are
checked, including replay; merged, missing or incompatible subjects are refused.
A version change on either subject makes the old dismissal ineligible. Review does
not increment subject versions or change examples. The migration also adds nullable
`run_candidate.compared_subject_version`; its write/read presentation work is still
pending, and existing result snapshots remain unchanged.

The read service returns up to ten compatible active candidates, excluding self,
merged/empty subjects and current dismissals before the limit. Exact cosine scores
sort descending with UUID tie-breaking. Candidate summaries, galleries and counts
are fetched in batches inside the same repeatable-read transaction.

Subject details now offer **Find potential matches**, **Dismiss**, **Show dismissed**
and **Restore**. Loading the page does not start a comparison. Cards include UUID,
name, gallery or missing-image message, counts, and similarity. **Review merge**
opens the existing source-grouped whole-subject preview, survivor selection and
explicit confirmation. The selected-example move workflow remains available.
Successful mutation responses release their browser idempotency key so a later
explicit dismiss after restoration is a new action; uncertain failed requests
retain their key for retry.

The documented GET/PUT/DELETE endpoints reuse authentication, feature availability,
rate limiting and Origin checks. Stale dismissals trigger refreshed subject details
and comparison. Error/retry rendering exists; dedicated fault-injection/browser
coverage and retrieval performance on migrated production-scale data remain pending.
Production UI review and final-schema integration also remain outstanding.

## Verification

Image: `sha256:dbc7556239132cfb43f8dc167affbea305023655c5c8fe08597947edaa3b8ce6`.
Source fingerprint: `8c7ac30fccc8c94ddcefc32fa819cd36f21cf1ca312d4e1f53a8a9aeda8a881a`.
These identify a local immutable build, not a published registry image.

`platform-validate-20260909-r8` succeeded: **170 tests, zero failures/errors/skips**,
162.837 seconds for the test stage. Fresh schema reached 011. Ruff and both
JavaScript syntax checks passed. OpenAPI YAML parsed and `git diff --check` passed.

New PostgreSQL/API checks cover deterministic ranking, a ten-result limit after
dismissals, model/empty/merged exclusions, both pair directions, restore and replay,
version changes and stale replays, audit counts, authentication/Origin checks, and
unchanged membership while viewing/reviewing suggestions. The Chromium test covers
on-demand loading, missing gallery, dismiss/restore/dismiss at unchanged versions,
merge preview and explicit survivor navigation, and invalidated candidate display.

Protected validation evidence and cleanup receipt:
`.local/platform-validate-20260909-r8/`. Build/source manifest:
`.local/platform-maintenance-build-20260909-r8/`. Validation containers were removed
by manifest-scoped cleanup. Evidence/image retention: platform implementation owner,
through 2026-10-09.

The same image's protected-copy migration rehearsal is running as
`platform-rehearsal-20260909-r8`. Read durable status with:

```bash
.test-venv/bin/python -m maintenance.local status \
  --artifacts /workspaces/ThunderCloud/.local/platform-rehearsal-20260909-r8
```

The `maintenance.local run --procedure rehearse-migration` wrapper performs scoped
cleanup on terminal completion. Its `progress.json` and eventual `report.json`
are in that directory. The input backup remains protected in
`.local/platform-copy-20260909/`. Check the terminal report before claiming 011
rehearsal acceptance. CPU limits are 0.75 for PostgreSQL and 0.25 for the runner;
heavy checks run sequentially. Final framework migration acceptance is separate.


## Saved-result work in progress

The next local build implements compared-version capture during ranking, enforces
it on new result writes, and adds unchanged/changed/unavailable result status with
browser messaging. Older correction evidence includes the completed source-split
receipt shape. Result reads retain recorded names, IDs and scores. The retained
handling policy now performs comparison while independently enrolling new subjects.
Preservation receipts also cover compared versions and suggestion dismissals.

Ten focused database/interactive unit tests passed; full PostgreSQL/browser
verification is pending. New checks cover immutable snapshots after edits/merges,
older unavailable/split evidence, retained comparison without automatic association,
browser result messages, suggestion error/retry, stale refresh and responsive
screenshots. Build image:
`sha256:1d418719c33abe9977137d93d5095eb13ecf11896b93041c2a8642d198c0d7e4`,
receipt `.local/platform-maintenance-build-20260909-r9/`.

A sequential wrapper at
`.local/platform-validation-after-rehearsal-20260909/run.py` waits on the exact r8
rehearsal container, requires its successful terminal report and cleanup export,
then starts `platform-validate-20260909-r9` using `maintenance.local run`.
No new database validation begins before that prerequisite. Inspect the r8
rehearsal and r9 validation artifact directories for current durable progress;
do not restart either procedure based on elapsed observation time.


Validation update: `platform-validate-20260909-r9` succeeded with **175 tests,
zero failures/errors/skips**, including saved-result and browser fault-injection
checks. Protected artifacts include responsive Potential matches screenshots.
The preceding `platform-rehearsal-20260909-r8` also succeeded through migration
011 with preservation and recovery/resume checks; scoped cleanup completed.
These results predate migration 012/operator input work, which uses focused checks
until the final integrated validation, per the user's updated execution preference.
