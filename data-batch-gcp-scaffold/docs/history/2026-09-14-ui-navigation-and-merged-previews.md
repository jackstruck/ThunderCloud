# Console navigation and merged previews — 2026-09-14

Deployed console revision `face-console-00035-nml`, ready with 100% traffic and
IAP enabled. Image:
`us-central1-docker.pkg.dev/teak-banner-dome/face-batch-worker/console@sha256:d0517ea389fa5cc4bae2a007c59f2f30cf7cb2c8eeb2b8a5639878aac37b3e07`.
The preceding console image remains protected for rollback. Acquisition and
processor executions continue on their existing images.

Changes:

- Full-width Check status; Next always occupies the right pagination column.
- Enrolled-subject cards put View Subject and View Source together above previews.
- Match cards retain external source-page links and add subject/source overview buttons.
- Compact subject controls: Merge, Move examples, New subject, Undo merge, with
  descriptive tooltips and responsive layout. Enrolled examples use View Run.
- Potential-match selection controls sit outside the card grid, with Clear selection.
- Source overviews remove the subject-search form and promote the original-source link.
- Visible keyboard focus and consistent disabled-control cursors.

Historical candidates now resolve merge chains when retrieving current gallery
images and platform sources. Original candidate IDs, ranks, similarity scores,
and names remain intact; `resolved_subject_id` supplies the current navigation
identity. Cards explain when they show a merged subject. Source references are
retrieved in one batch query, avoiding a request per candidate.

Read-only verification on run `26f0ca69-863f-4d12-b644-6751bf2f1356` confirmed
ranks 9 and 10 had zero previews before the change and five each afterward. Both
resolve to `03f541ba-f3b9-58b8-898b-9f7617a72c05`; scores remain `0.36694753`
and `0.3623584`. No stored run or subject data was modified for this fix.

Validation: 22 API/result unit tests; seven Node DOM/import tests; JS syntax and
diff checks; container HTTP smoke checks for the served assets; Cloud Run readiness,
image digest, traffic, and IAP verification. No Chromium testing was performed.
DOM tests can be repeated with `npm ci --prefix tests/ui` and
`npm test --prefix tests/ui` from the scaffold directory.

Follow-up deployment: `face-console-00036-sxk`, ready with 100% traffic and IAP
preserved. Image:
`us-central1-docker.pkg.dev/teak-banner-dome/face-batch-worker/console@sha256:a570be0b92ae22f4f76a5d81863db5d8ad2793ca64547731c4c86910d14fa646`.

The source header now contains the source ID and a same-row Open button, with no
URL printed underneath. Next retains the secondary style and right-hand grid
column. Select all groups and Clear group selection occupy equal-width columns.
Shift-click selection handles original label clicks directly, cancelling their
forwarded checkbox clicks so Shift is not lost; range selection keeps a stable
anchor. Regression checks cover selecting and clearing ranges via labels and the
group-selection toolbar. Five DOM tests passed, plus the four unchanged import
tests in the preceding check. No browser engine was launched.
