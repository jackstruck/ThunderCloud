CREATE INDEX IF NOT EXISTS media_run_principal_created_idx
    ON media_run (submitter_principal, created_at DESC);
