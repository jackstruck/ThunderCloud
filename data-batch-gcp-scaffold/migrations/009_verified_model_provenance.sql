-- Administrative evidence for a historical job whose model label was imprecise.
-- Runtime services read this record; they cannot approve model equivalence.
CREATE TABLE IF NOT EXISTS verified_embedding_model (
    processing_job_id uuid PRIMARY KEY REFERENCES processing_job(job_id),
    reported_model_version text NOT NULL,
    verified_model_version text NOT NULL,
    evidence jsonb NOT NULL,
    verified_by text NOT NULL,
    verified_at timestamptz NOT NULL DEFAULT now(),
    CHECK (reported_model_version <> verified_model_version)
);
