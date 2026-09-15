CREATE EXTENSION IF NOT EXISTS pg_trgm;

ALTER TABLE subject
    ADD COLUMN IF NOT EXISTS row_version bigint NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS merged_into_subject_id uuid REFERENCES subject(subject_id);
ALTER TABLE identity ADD COLUMN IF NOT EXISTS row_version bigint NOT NULL DEFAULT 1;
ALTER TABLE media_run ADD COLUMN IF NOT EXISTS selection_fingerprint text;
ALTER TABLE submission_enrollment DROP CONSTRAINT IF EXISTS submission_enrollment_decision_check;
ALTER TABLE submission_enrollment ADD CONSTRAINT submission_enrollment_decision_check
    CHECK (decision IN ('matched','created','assigned'));

CREATE INDEX IF NOT EXISTS identity_display_name_search_idx
    ON identity USING gin (lower(display_name) gin_trgm_ops);

CREATE TABLE IF NOT EXISTS subject_example (
    example_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    subject_id uuid NOT NULL REFERENCES subject(subject_id),
    source_id uuid NOT NULL REFERENCES source_asset(source_id),
    face_track_id uuid UNIQUE REFERENCES face_track(track_id),
    submission_group_id uuid UNIQUE REFERENCES submission_face_group(group_id),
    embedding vector(512) NOT NULL,
    model_version text NOT NULL,
    start_ms bigint,
    end_ms bigint,
    quality_score real,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((face_track_id IS NOT NULL)::integer + (submission_group_id IS NOT NULL)::integer = 1)
);
CREATE INDEX IF NOT EXISTS subject_example_subject_idx ON subject_example(subject_id, example_id);
CREATE INDEX IF NOT EXISTS subject_example_source_idx ON subject_example(source_id);
CREATE INDEX IF NOT EXISTS subject_browse_idx ON subject(subject_id) WHERE merged_into_subject_id IS NULL;
ALTER TABLE subject_representative_face
    ADD COLUMN IF NOT EXISTS example_id uuid REFERENCES subject_example(example_id);
CREATE INDEX IF NOT EXISTS representative_example_idx ON subject_representative_face(example_id);

CREATE OR REPLACE FUNCTION protect_subject_example_origin() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF ROW(NEW.source_id, NEW.face_track_id, NEW.submission_group_id, NEW.embedding::text,
           NEW.model_version, NEW.start_ms, NEW.end_ms)
       IS DISTINCT FROM
       ROW(OLD.source_id, OLD.face_track_id, OLD.submission_group_id, OLD.embedding::text,
           OLD.model_version, OLD.start_ms, OLD.end_ms) THEN
        RAISE EXCEPTION 'Enrollment example origins and embeddings are immutable';
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS protect_subject_example_origin ON subject_example;
CREATE TRIGGER protect_subject_example_origin BEFORE UPDATE ON subject_example
    FOR EACH ROW EXECUTE FUNCTION protect_subject_example_origin();

CREATE TABLE IF NOT EXISTS enrollment_assignment (
    assignment_id uuid PRIMARY KEY,
    run_id uuid NOT NULL REFERENCES media_run(run_id),
    destination text NOT NULL CHECK (destination IN ('new', 'existing')),
    target_subject_id uuid REFERENCES subject(subject_id),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (assignment_id, run_id),
    CHECK ((destination = 'existing') = (target_subject_id IS NOT NULL))
);
CREATE TABLE IF NOT EXISTS enrollment_assignment_member (
    group_id uuid PRIMARY KEY REFERENCES submission_face_group(group_id),
    assignment_id uuid NOT NULL REFERENCES enrollment_assignment(assignment_id)
);
CREATE INDEX IF NOT EXISTS assignment_run_idx ON enrollment_assignment(run_id);

CREATE TABLE IF NOT EXISTS subject_change_event (
    operation_id uuid PRIMARY KEY,
    actor text NOT NULL,
    action text NOT NULL CHECK (action IN ('edit', 'combine', 'move')),
    request_fingerprint text NOT NULL,
    details jsonb NOT NULL,
    result jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- Existing merge redirects predate the dedicated column.
UPDATE subject s SET merged_into_subject_id = target.subject_id
FROM subject target
WHERE s.merged_into_subject_id IS NULL
  AND s.metadata->>'merged_into' = target.subject_id::text
  AND s.subject_id <> target.subject_id;
