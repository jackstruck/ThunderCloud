-- Preserve a single face crop separately from video selection contact sheets.
ALTER TABLE submission_face_group
    ADD COLUMN IF NOT EXISTS representative_object_name text,
    ADD COLUMN IF NOT EXISTS representative_generation bigint;
