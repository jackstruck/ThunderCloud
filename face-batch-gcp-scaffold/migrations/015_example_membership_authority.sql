DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM face_track t LEFT JOIN subject_example e ON e.face_track_id=t.track_id
               WHERE t.subject_id IS NOT NULL AND (e.example_id IS NULL OR e.subject_id<>t.subject_id))
       OR EXISTS (SELECT 1 FROM submission_enrollment s
                  LEFT JOIN subject_example e ON e.submission_group_id=s.group_id
                  WHERE e.example_id IS NULL OR e.subject_id<>s.subject_id) THEN
        RAISE EXCEPTION 'Membership must be reconciled into examples before migration';
    END IF;
END $$;
ALTER TABLE face_track DROP COLUMN subject_id;
ALTER TABLE submission_enrollment DROP COLUMN subject_id;
