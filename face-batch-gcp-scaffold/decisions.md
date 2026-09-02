# Probe implementation decisions

Status: **all seven local-probe decisions approved by the operator**. These decisions
supersede conflicting durable-probe requirements in `PROBE_IMPLEMENTATION_PLAN.md`.

## 1. Probe-media retention — decided

- Operator decision: do not store locally submitted probe media in GCS or Cloud SQL.
- Local files remain under the operator's control and are read directly for inference.
- Return the embedding-derived rankings and provenance only in the active response;
  decision 2 confirms that no probe record or result snapshot is retained.
- Do not store local absolute paths, media bytes, crops, or an artificial durable-media
  URI in the database.
- Consequence: the implementation plan's durable `face-probes/` upload requirement is
  superseded for local submissions, and there is no probe-media retention period or
  probe-media deletion operation to configure.
- Operator selection: **no server-side probe-media storage**.

## 2. Probe-record retention — decided

- Operator decision: do not retain probe records or results.
- Return the detected faces/tracks, embeddings-derived decisions, ranked candidates,
  and source-video provenance only in the active command response.
- Do not persist probe embeddings, ranked matches, provenance snapshots, request keys,
  or probe status rows in Cloud SQL.
- Do not include embeddings themselves in the response or logs.
- Consequence: `face-probe show` is removed because no durable probe snapshot exists.
- Gallery queries remain read-only and no probe data is promoted into training data.
- Operator selection: **no probe-result retention**.

## 3. Match threshold and version — decided

- Operator decision: do not make an automatic `matched` identity decision.
- Return deterministically ranked candidate subjects and cosine-similarity scores for
  operator review.
- Describe results as candidates, not confirmed matches.
- No probe threshold or threshold-version configuration is required for the initial
  tool.
- Do not associate an identity or modify the gallery based on a probe result.
- If automated decisions are introduced later, require a representative same-person
  and different-person evaluation set and documented false-accept/false-reject targets.
- Operator selection: **ranking only; automatic matching disabled**.

## 4. Media types and limits — decided

- Accepted images: JPEG and PNG.
- Maximum encoded image size: 25 MiB (26,214,400 bytes).
- Maximum decoded image size: 50 million pixels.
- Accepted videos: MP4.
- Maximum encoded video size: 250 MiB (262,144,000 bytes).
- Maximum video duration: 15 minutes (900 seconds).
- Corpus basis: the largest object in `bulk-download/data/manifest.jsonl` is
  155,542,524 bytes (about 148.3 MiB), so the selected video-byte limit accommodates
  every current training video with headroom.
- Operator selection: **JPEG/PNG 25 MiB and 50 MP; MP4 250 MiB and 15 minutes**.

## 5. Identity display names — decided

- Operator decision: return `identity.display_name` with each ranked candidate when the
  candidate subject has an associated identity row.
- Return `null` when the subject has no identity or the identity has no display name.
- Treat `display_name` as an operator-managed label that may contain a username or
  another privacy-preserving identifier, not as model-derived or independently verified
  identity evidence.
- Continue returning the subject UUID, similarity score, and source-video provenance so
  the label is never the only candidate identifier.
- Do not create or modify identity assignments during probing.
- Operator selection: **include nullable `identity.display_name`**.

## 6. Initial authorized principals — decided

- Operator decision: keep probing local-only until it has been fully tested.
- Use the developer's existing ADC and Cloud SQL read permissions to access the gallery.
- Do not add probe-specific GCS IAM because local probe media is not uploaded.
- Do not create a probe service account, service-account key, public endpoint, remote
  runtime, or multi-user authorization layer.
- Do not collect a claimed submitter principal because no probe record is retained.
- Before any shared or remote use, separately design authenticated submit, view,
  deletion, and enrollment/promotion boundaries.
- Operator selection: **local access only during development and testing**.

## 7. Local review output — decided

- Operator decision: provide local ephemeral review artifacts so the developer can
  confirm exactly which detected faces or retained video tracks were compared.
- Write a JSON result and face/track review crops beneath a unique local temporary
  probe directory.
- Create directories with mode `0700` and files with mode `0600`; never overwrite an
  existing output directory or file.
- Include crop identifiers in JSON so each crop maps unambiguously to its ranked
  candidate group.
- Never upload crops, store them in Cloud SQL, include them as JSON byte payloads, or
  log their image contents.
- Print the local review-directory path for the developer. Local artifacts remain under
  the developer's control and may be deleted after review.
- Operator selection: **private local JSON and review crops; no server-side storage**.

## Approved decision summary

```text
Probe media retention: none; local submissions are never uploaded [decided]
Probe record retention: none; results exist only for the active response [decided]
Probe decision: ranked candidates only; no automatic threshold [decided]
Allowed media and limits: JPEG/PNG 25 MiB and 50 MP; MP4 250 MiB and 15 minutes [decided]
Include identity display names: yes, when present [decided]
Initial access: local-only through existing developer ADC and database permissions [decided]
Local review output: private temporary JSON and crops, never uploaded [decided]
```

No probe schema, GCS prefix, probe IAM binding, migration, or cloud write test is needed
for this local read-only design. Any future remote or durable probe service requires a
new authorization and design review.
