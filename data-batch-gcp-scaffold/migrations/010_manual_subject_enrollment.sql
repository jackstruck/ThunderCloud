-- Historical policy labels remain evidence; new writes create independent subjects.
ALTER TABLE media_run ADD COLUMN IF NOT EXISTS association_policy text NOT NULL
    DEFAULT 'legacy_v1' CHECK (association_policy IN ('legacy_v1','manual_new_v1'));
ALTER TABLE media_run ALTER COLUMN association_policy SET DEFAULT 'manual_new_v1';
ALTER TABLE processing_job ADD COLUMN IF NOT EXISTS association_policy text NOT NULL
    DEFAULT 'legacy_v1' CHECK (association_policy IN ('legacy_v1','manual_new_v1'));
ALTER TABLE processing_job ALTER COLUMN association_policy SET DEFAULT 'manual_new_v1';
