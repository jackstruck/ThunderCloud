DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM enrollment_assignment a JOIN media_run r USING(run_id)
               WHERE a.destination='existing' AND r.state NOT IN ('succeeded','failed','cancelled','expired')) THEN
        RAISE EXCEPTION 'Pending existing-subject assignments require explicit disposition before migration';
    END IF;
END $$;
ALTER TABLE enrollment_assignment DROP COLUMN destination, DROP COLUMN target_subject_id;
