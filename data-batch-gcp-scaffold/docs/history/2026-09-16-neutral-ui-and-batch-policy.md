# Neutral submission copy, full-width actions, and requested batch policy change

The link label is now “Media, video, or user link”. The provider-specific explanatory
paragraph was removed, and profile-validation/unsupported-link messages use neutral
wording. Both submission actions fill their forms' width. Existing provider routing
is unchanged. Console revision `face-console-00045-25v` serves the update with image
`sha256:010caa75209ae8565aae85c34206f9df62d69620064f1c7c0b49048a6a342c09`.
The existing console/profile unit tests passed, and local/live browser checks verified
neutral visible copy and full-width buttons at 390px and 1280px without overflow.

The user requested changing batch `15372e4f-5afa-5f63-97fb-df8f48d77708` from
`search_then_discard` to `retain_and_enroll`. Its acquisition execution
`face-acquisition-batch-7gvzs` was cancelled before editing the latest checkpoint with
a generation precondition. Video selection and the acquisition manifest were retained;
prior receipts and execution attribution were preserved in policy history. New handoff
receipts use policy-specific operation keys. The replacement acquisition execution is
`face-acquisition-batch-jdbbl`.

The acquisition processor now reuses archive runs only for the requested handling
policy, so earlier search-only runs do not prevent retention and enrollment. A real
PostgreSQL test verifies policy-specific reuse, and the acquisition unit tests passed.
Acquisition image: `sha256:cfbf8e1af3c969a03be9392684a097ab1730687eb06920f82cef3b8325c51173`.
Private checkpoint backups and deployment evidence remain under `.local/neutral-ui`.

The replacement execution picked up `retain_and_enroll` and resumed processing all
150 selected videos from the preserved checkpoint. Three newly submitted run IDs
were independently checked through the live console API and all reported
`handling_policy=retain_and_enroll`. Matching prior retention runs were reused;
search-only runs no longer block new retention/enrollment handoff.
