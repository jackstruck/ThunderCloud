# Disable example UI — 2026-09-16

The subject page now has one Disable example action, a generic Disabled status,
and Restore example. Bulk actions no longer require a reason selection. Disable
selected and Restore selected occupy equal columns across the full controls row,
with explanatory text below. Existing disabled examples remain restorable; the
API's existing unusable_face value is retained for compatibility.

Console image: sha256:577ae3e169ec04c3a627dd0cc2c1019f543027bb17be0f3429caedb3afeb8a75.
The deployment changes only console UI assets, layered on the previously deployed
console image. Terraform's console image variable is updated locally. A targeted
Terraform plan included unrelated label changes, so the service image was updated
directly instead; that plan was not applied.

Matching-group selection summary and apply action now follow the group selection
buttons. Manual subject selection puts Select all and Clear selection on one row
and Merge full width below. The two selection helper labels were removed, and
Include excluded is now Show excluded.

Validation: JavaScript syntax and diff whitespace checks passed. Read-only live
Playwright checks verified deployed asset bytes, equal-width disable/restore and
manual selection buttons at 390px and 1280px, full-width manual Merge below,
removed helper labels, Show excluded, and the group apply bar immediately after
the selection row (including after rerender). No subjects were changed by checks.
