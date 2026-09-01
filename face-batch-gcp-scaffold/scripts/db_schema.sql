-- Run once as a Cloud SQL administrative database user against database face_index.
-- After bootstrap, grant the Batch IAM database user only the DML/sequence privileges it needs.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS identity (
    identity_id       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    external_identity_ref text UNIQUE,
    display_name      text,
    metadata          jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS subject (
    subject_id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    identity_id       uuid REFERENCES identity(identity_id) ON DELETE SET NULL,
    canonical_embedding vector(512),
    model_version     text NOT NULL,
    sample_count      integer NOT NULL DEFAULT 0,
    metadata          jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS source_asset (
    source_id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    external_source_ref text NOT NULL,
    source_sha256      char(64) NOT NULL,
    captured_at        timestamptz,
    metadata           jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at         timestamptz NOT NULL DEFAULT now(),
    UNIQUE (external_source_ref, source_sha256)
);

CREATE TABLE IF NOT EXISTS processing_job (
    job_id             uuid PRIMARY KEY,
    idempotency_key    text NOT NULL UNIQUE,
    source_id          uuid NOT NULL REFERENCES source_asset(source_id) ON DELETE RESTRICT,
    batch_job_name     text,
    worker_version     text NOT NULL,
    detector_version   text NOT NULL,
    embedding_model_version text NOT NULL,
    threshold_version  text NOT NULL,
    status             text NOT NULL CHECK (status IN ('queued','running','succeeded','failed')),
    error_code         text,
    started_at         timestamptz,
    completed_at       timestamptz,
    created_at         timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS face_track (
    track_id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id          uuid NOT NULL REFERENCES source_asset(source_id) ON DELETE RESTRICT,
    processing_job_id  uuid NOT NULL REFERENCES processing_job(job_id) ON DELETE RESTRICT,
    subject_id         uuid REFERENCES subject(subject_id) ON DELETE SET NULL,
    local_track_id     integer NOT NULL,
    start_ms           bigint NOT NULL,
    end_ms             bigint NOT NULL,
    aggregate_embedding vector(512) NOT NULL,
    model_version      text NOT NULL,
    observation_count  integer NOT NULL,
    embedded_count     integer NOT NULL,
    max_quality        real,
    mean_quality       real,
    best_candidate_subject_id uuid REFERENCES subject(subject_id) ON DELETE SET NULL,
    best_candidate_score real,
    decision           text NOT NULL CHECK (decision IN ('matched','unknown','deferred')),
    created_at         timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source_id, processing_job_id, local_track_id)
);

-- Cosine-distance HNSW indexes. Tune m/ef_construction and maintenance settings after benchmarking.
CREATE INDEX IF NOT EXISTS subject_embedding_hnsw
    ON subject USING hnsw (canonical_embedding vector_cosine_ops)
    WHERE canonical_embedding IS NOT NULL;

CREATE INDEX IF NOT EXISTS face_track_embedding_hnsw
    ON face_track USING hnsw (aggregate_embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS face_track_source_idx ON face_track(source_id);
CREATE INDEX IF NOT EXISTS face_track_subject_idx ON face_track(subject_id);
CREATE INDEX IF NOT EXISTS processing_job_source_idx ON processing_job(source_id);

-- Privileges are intentionally applied separately by db_grants.sql.
