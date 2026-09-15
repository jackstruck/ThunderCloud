# Within-source matching groups

The console and `source-merges` CLI use the same authenticated source-scoped service.
No enrollment, acquisition, downloads, GPU work, or schema migration is needed.

## Operator workflow

Open a source and use **Find matching groups**, entering an explicit cosine threshold.
On Sources, select specific sources first; scans run sequentially and each source has
its own candidate pool and results. No whole-library scan is implied by a search.

Review member previews, time spans, identity details, and the minimum pairwise score.
Groups start unselected. Select the groups to apply, choose **Keep subject**, and
explicitly acknowledge conflicting populated identity details. The survivor retains
its identity details. **Exclude member** leaves at least two members and a conservative
lower bound on the group's minimum score; the server revalidates all remaining pairs
at application. **Fix a mismatch** opens the correction controls. **These are different people** records the ordinary versioned pair dismissal;
rescan before applying that group. Singletons remain unchanged. Different recognition
models are displayed as separate partitions and never compared. Multi-source subjects
are excluded based on current example membership and are never split automatically.

**Merge selected groups** applies directly without another modal. The ordinary manual
subject selection below it remains a separate whole-subject operation. Each proposed
group is an independent atomic transaction; completed groups survive later failures.
The browser sends one group at a time and retains reviews and original operation IDs
in session storage across reloads. A failed or stale group stays visible. Unresolved
requests are frozen for retry; do not rescan an uncertain operation into a new ID.

Outcomes distinguish `merged`, `skipped`, `stale`, and `failed`. Retry an uncertain
request unchanged; the persisted merge event returns the original result without
another merge. Definitive stale/failed results are also retained after rollback; changing an admitted operation payload conflicts. Failure records contain no moved membership and do not appear in separation history. Rescan stale groups
for new versions, membership, and operation IDs. Newly enrolled subjects only enter
the next scan. Current dismissals and identity versions are checked again under the
same advisory transaction lock used by enrollment and manual corrections.

Result links open the survivor, where **Separate a previous merge** remains available.
This recovery depends on unchanged member examples and the existing recent-history
limit (20 merge events); it is not an unconditional undo. Source attribution and
representative-face lineage are preserved by merging and separation.

## Local command

Install the project in your Python environment (`pip install -e .`). Sign in with
`gcloud auth application-default login` if needed. The deployed CLI uses
`--impersonate-service-account face-batch-merge-cli@teak-banner-dome.iam.gserviceaccount.com`.
Terraform grants this keyless account only console IAP access and allows the existing
approved console principal to sign its short-lived request JWTs. It has no database,
storage, or job permissions. IAM signing audit logs identify the human caller; merge
events identify the CLI service account. Resume a request using the same actor.

An optional `--token-file` can instead supply a private IAP-authorized JWT. User ADC
ID tokens also work where an organization-owned OAuth client has been explicitly
allowed by IAP; the shared Google SDK OAuth client is not accepted by this deployment.
No credentials are written into plans or receipts. HTTPS is required, redirects are
not followed, and each keyless JWT is scoped to the exact API URL for ten minutes.
This follows [Google's service-account JWT authentication procedure](https://docs.cloud.google.com/iap/docs/authentication-howto#authenticate_with_a_service_account_jwt).

```bash
source-merges --origin https://CONSOLE_HOST \
  --impersonate-service-account OPERATOR_SERVICE_ACCOUNT plan \
  --source SOURCE_UUID --source SECOND_SOURCE_UUID \
  --threshold REVIEWED_COSINE_THRESHOLD --output private/plan.json

# Alternative explicit source list: --sources-file private/sources.json
# sources.json is a JSON array of source UUIDs.

# Review the plan. Set selected=true only for approved groups, choose survivor_id,
# optionally remove members, and set review_identity_conflicts=true after review.
source-merges --origin https://CONSOLE_HOST \
  --impersonate-service-account OPERATOR_SERVICE_ACCOUNT apply \
  --input private/plan.json --receipt private/outcomes.json --reviewed

# Resume with the identical command and files after an interrupted response.
```

Plan and receipt files are versioned JSON, atomically written with mode 0600 and
fsync; lock files serialize local writers. Planning refuses to overwrite an existing
plan. Application writes the exact request to its receipt before sending it, saves
each outcome, skips acknowledged successful operations on resume, and refuses to
reuse a submitted operation ID with an edited request. Use a new plan file for a
rescan, retaining previous receipts. No CLI command writes directly to subject tables.

## Bounds and algorithm

`complete-linkage-v1` selects the eligible cluster pair with the greatest minimum
cross-pair cosine; sorted subject IDs break ties. A missing/dismissed pair is a hard
exclusion. Stored single-source canonical vectors must be finite, nonzero, 512-dimensional,
with positive matching sample counts and consistent example models. Suggested survivors
have the most current examples, then the smallest subject ID.

Each source scan has a 400-current-subject ceiling (including excluded candidates),
at most 79,800 pair comparisons, and a 10-second elapsed budget. Remaining SQL time
is bounded at each query; grouping is checked before a plan is returned. An oversized
or timed-out source returns `background_scan_required`, with no partial plan. Bounded
background scanning is a separately reviewed future extension. Proposals over 50
members are marked for manual handling and cannot be submitted as a large merge;
the planner never splits them to evade that limit. Application accepts at most 200
disjoint groups / 400 members per request; the provided clients send one group at a time.

OpenAPI documents `/api/sources/{source_id}/merge-proposals` and its `/apply` route.
Both require normal console authentication and an exact Origin header. The merge event
retains algorithm, threshold, source, model, expected identity versions, proposed score,
and the independently verified minimum score alongside ordinary recovery membership.

## Rollout and review evidence

1. Deploy with `source_merges_apply_enabled=false` (the default). Generate proposals
   on an explicitly chosen small production source set. An exploratory score is not
   a calibrated threshold; similarity is never an identity probability.
2. Review examples from the deployed model and record outcomes using
   `source-merges-review.example.json`. Record false merges and missed matches in
   separate arrays; include the model, threshold, source, member IDs, reviewer, and
   actual observation. Record operation IDs for applied outcomes. Do not report a
   false-merge rate without its reviewed denominator, or infer recall from proposals
   alone: missed matches require review of excluded/unproposed pairs too. Once the
   reviewed threshold is accepted, set `source_merges_apply_enabled=true` in Terraform
   to enable explicitly selected operator groups. No numerical threshold is preset.
3. Unattended application is not implemented or enabled. It requires separately
   reviewed calibration evidence establishing an acceptable false-merge rate.

Retain review records privately alongside plans/receipts. Recalibrate after a model
change. Failed groups, user rejections, and missed matches are not interchangeable.

## Current operator rollout

Operator group application is enabled following user review of source
`2f8d380a-1391-5f4b-9a8a-1b0f628bc48e` at cosine `0.5` with deployed model
`adaface-ir18-6b6a3577`. All eight proposed groups were reported as internally correct;
some distinct groups still represent the same person. This is a useful reviewed
starting point for operator selection, not a universal threshold or an unattended
accuracy guarantee. The group-level assessment and separate missed-match observation
are retained privately in `.local/source-merges-reviewed/review.json`.

The review panel starts collapsed; open **Matching groups** to scan or review saved groups.
After every selected group merges successfully, the panel collapses and its heading
shows the merged group count. Failed or unresolved applications remain open for review.
Group cards are numbered and show the number of subjects becoming one. The member
being kept is marked; full IDs and extra identity details can be expanded. Use
**Select all groups** only after review, or select groups individually. The selection
summary states the exact number of subjects and resulting groups before application.
Application availability is checked against the current server setting whenever the
review panel opens. Reload to pick up a rollout change; saved selections are retained.
