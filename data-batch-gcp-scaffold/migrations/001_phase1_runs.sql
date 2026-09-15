-- Additive Phase 1 schema. Apply after scripts/db_schema.sql.
CREATE TABLE IF NOT EXISTS media_run (
    run_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    submitter_principal text NOT NULL,
    idempotency_key text NOT NULL,
    request_fingerprint char(64) NOT NULL,
    handling_policy text NOT NULL CHECK (handling_policy = 'search_then_discard'),
    source_kind text NOT NULL CHECK (source_kind IN ('url', 'upload')),
    source_page_url text,
    source_adapter text,
    content_type text,
    expected_bytes bigint CHECK (expected_bytes IS NULL OR expected_bytes > 0),
    source_sha256 char(64),
    object_name text,
    object_generation bigint,
    object_bytes bigint CHECK (object_bytes IS NULL OR object_bytes > 0),
    state text NOT NULL CHECK (state IN (
      'awaiting_media','fetching','queued','detecting','awaiting_face_selection',
      'matching','enrolling','succeeded','failed','cancelled','expired'
    )),
    outcome text CHECK (outcome IS NULL OR outcome IN ('candidates','no_faces')),
    failed_step text,
    retryable boolean NOT NULL DEFAULT false,
    error_code text,
    cancel_requested boolean NOT NULL DEFAULT false,
    row_version bigint NOT NULL DEFAULT 1,
    expires_at timestamptz NOT NULL DEFAULT now() + interval '7 days',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    UNIQUE (submitter_principal, idempotency_key),
    CHECK ((source_kind = 'url') = (source_page_url IS NOT NULL)),
    CHECK ((state <> 'failed') OR (failed_step IS NOT NULL AND error_code IS NOT NULL)),
    CHECK ((outcome <> 'no_faces') OR state = 'succeeded')
);

CREATE INDEX IF NOT EXISTS media_run_expiry_idx ON media_run (expires_at);
CREATE INDEX IF NOT EXISTS media_run_active_principal_idx
    ON media_run (submitter_principal, state)
    WHERE state NOT IN ('succeeded','failed','cancelled','expired');

CREATE TABLE IF NOT EXISTS submission_face_group (
    group_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id uuid NOT NULL REFERENCES media_run(run_id) ON DELETE CASCADE,
    local_group_id integer NOT NULL,
    bbox jsonb,
    start_ms bigint,
    end_ms bigint,
    quality_summary jsonb NOT NULL DEFAULT '{}'::jsonb,
    preview_object_name text NOT NULL,
    preview_generation bigint NOT NULL,
    detector_version text NOT NULL,
    embedding_model_version text NOT NULL,
    aggregate_embedding vector(512) NOT NULL,
    selected boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (run_id, local_group_id),
    CHECK ((bbox IS NOT NULL) <> (start_ms IS NOT NULL AND end_ms IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS run_candidate (
    run_id uuid NOT NULL REFERENCES media_run(run_id) ON DELETE CASCADE,
    group_id uuid NOT NULL REFERENCES submission_face_group(group_id) ON DELETE CASCADE,
    subject_id uuid NOT NULL REFERENCES subject(subject_id) ON DELETE RESTRICT,
    rank integer NOT NULL CHECK (rank > 0),
    similarity real NOT NULL,
    display_name_snapshot text,
    source_count integer NOT NULL DEFAULT 0 CHECK (source_count >= 0),
    observation_count integer NOT NULL DEFAULT 0 CHECK (observation_count >= 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, group_id, subject_id),
    UNIQUE (run_id, group_id, rank)
);

CREATE TABLE IF NOT EXISTS run_operation (
    operation_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id uuid NOT NULL REFERENCES media_run(run_id) ON DELETE CASCADE,
    kind text NOT NULL CHECK (kind IN ('fetch','detect','match','deletion','promotion')),
    state text NOT NULL CHECK (state IN ('queued','leased','succeeded','failed')),
    attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    lease_owner uuid,
    lease_expires_at timestamptz,
    object_name text,
    object_generation bigint,
    last_error_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (run_id, kind),
    CHECK ((state <> 'leased') OR (lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS run_operation_claim_idx
    ON run_operation (kind, state, lease_expires_at, created_at);

CREATE TABLE IF NOT EXISTS subject_representative_face (
    representative_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    subject_id uuid NOT NULL REFERENCES subject(subject_id) ON DELETE CASCADE,
    source_id uuid REFERENCES source_asset(source_id) ON DELETE SET NULL,
    source_track_id uuid REFERENCES face_track(track_id) ON DELETE SET NULL,
    object_name text NOT NULL,
    object_generation bigint NOT NULL,
    content_type text NOT NULL CHECK (content_type = 'image/jpeg'),
    source_timestamp_ms bigint,
    quality_score real,
    active boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    retired_at timestamptz,
    UNIQUE (object_name, object_generation),
    CHECK ((active AND retired_at IS NULL) OR NOT active)
);

CREATE INDEX IF NOT EXISTS subject_representative_active_idx
    ON subject_representative_face (subject_id, active, created_at DESC) WHERE active;

CREATE TABLE IF NOT EXISTS gallery_cleanup_object (
    cleanup_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    representative_id uuid NOT NULL,
    object_name text NOT NULL,
    object_generation bigint NOT NULL,
    delete_after timestamptz NOT NULL DEFAULT now() + interval '1 day',
    state text NOT NULL DEFAULT 'queued'
      CHECK (state IN ('queued','leased','succeeded','failed')),
    attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    lease_owner uuid,
    lease_expires_at timestamptz,
    last_error_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (object_name, object_generation),
    CHECK ((state <> 'leased') OR (lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS gallery_cleanup_claim_idx
    ON gallery_cleanup_object (state, delete_after, lease_expires_at);

CREATE TABLE IF NOT EXISTS run_cleanup_object (
    cleanup_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id uuid NOT NULL REFERENCES media_run(run_id) ON DELETE CASCADE,
    object_name text NOT NULL,
    object_generation bigint NOT NULL,
    state text NOT NULL DEFAULT 'queued'
      CHECK (state IN ('queued','leased','succeeded','failed')),
    attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    lease_owner uuid,
    lease_expires_at timestamptz,
    last_error_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (object_name, object_generation),
    CHECK ((state <> 'leased') OR (lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS run_cleanup_claim_idx
    ON run_cleanup_object (state, lease_expires_at, created_at);
