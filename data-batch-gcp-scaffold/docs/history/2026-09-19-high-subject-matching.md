# High-subject source matching

The user authorized a one-time0.3 pass on sources with >70 current subjects, then
changed the unfinished sources to0.4. Initial inventory:13sources/1,130subjects.
The first source completed at0.3 (142->16,147examples preserved). Completed work
is retained; remaining sources use0.4. Per-source scan budget300seconds. Default
upload threshold and10-second interactive scanning are unchanged.

A separate follow-on job waits for that pass's completed, error-free report, then
inventories sources with >40 current subjects and applies0.4 group matching. This
inventory is taken after the predecessor finishes. Source/exclusion/identity checks
remain active and every transaction checks example counts are preserved. Matching
does not force a target count; sources may remain above the eligibility threshold.

Operational details, job/report identifiers and recovery notes are in the root
SESSION_HANDOFF.md. The follow-on is queued, not yet reported complete.
