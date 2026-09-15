DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM subject_representative_face g
               LEFT JOIN subject_example e USING(example_id)
               WHERE g.active AND (e.example_id IS NULL OR g.subject_id<>e.subject_id
                 OR g.source_id IS DISTINCT FROM e.source_id)) THEN
        RAISE EXCEPTION 'Active gallery ownership must be reconciled before migration';
    END IF;
END $$;
ALTER TABLE subject_representative_face
    DROP COLUMN subject_id, DROP COLUMN source_id, DROP COLUMN source_track_id,
    ADD CONSTRAINT active_gallery_requires_example CHECK (NOT active OR example_id IS NOT NULL);
CREATE INDEX gallery_example_active_idx ON subject_representative_face(example_id) WHERE active;
