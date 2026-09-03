-- Phase 2 retained enrollment. Apply after 001_phase1_runs.sql.
ALTER TABLE media_run DROP CONSTRAINT IF EXISTS media_run_handling_policy_check;
ALTER TABLE media_run ADD CONSTRAINT media_run_handling_policy_check
    CHECK (handling_policy IN ('search_then_discard', 'retain_and_enroll'));

ALTER TABLE media_run
    ADD COLUMN IF NOT EXISTS retained_source_id uuid REFERENCES source_asset(source_id),
    ADD COLUMN IF NOT EXISTS threshold_version text,
    ADD COLUMN IF NOT EXISTS enrollment_completed_at timestamptz;

ALTER TABLE source_asset
    ADD COLUMN IF NOT EXISTS object_name text,
    ADD COLUMN IF NOT EXISTS object_generation bigint,
    ADD COLUMN IF NOT EXISTS object_bytes bigint,
    ADD COLUMN IF NOT EXISTS content_type text,
    ADD COLUMN IF NOT EXISTS encryption_mode text,
    ADD COLUMN IF NOT EXISTS deleted_at timestamptz,
    ADD COLUMN IF NOT EXISTS deletion_principal text;

CREATE UNIQUE INDEX IF NOT EXISTS retained_source_digest_idx
    ON source_asset (source_sha256)
    WHERE object_name IS NOT NULL AND deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS submission_enrollment (
    group_id uuid PRIMARY KEY REFERENCES submission_face_group(group_id) ON DELETE RESTRICT,
    run_id uuid NOT NULL REFERENCES media_run(run_id) ON DELETE RESTRICT,
    source_id uuid NOT NULL REFERENCES source_asset(source_id) ON DELETE RESTRICT,
    subject_id uuid NOT NULL REFERENCES subject(subject_id) ON DELETE RESTRICT
        DEFERRABLE INITIALLY DEFERRED,
    decision text NOT NULL CHECK (decision IN ('matched', 'created')),
    candidate_subject_id uuid REFERENCES subject(subject_id) ON DELETE SET NULL,
    candidate_similarity real,
    embedding_model_version text NOT NULL,
    threshold_version text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (run_id, group_id)
);

CREATE INDEX IF NOT EXISTS submission_enrollment_subject_idx
    ON submission_enrollment (subject_id);
