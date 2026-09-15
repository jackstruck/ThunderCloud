ALTER TABLE submission_face_group
    ADD COLUMN enrollment_decision text CHECK(enrollment_decision IN ('matched','created','assigned')),
    ADD COLUMN enrolled_at timestamptz,
    ADD CONSTRAINT enrollment_evidence_complete CHECK ((enrollment_decision IS NULL)=(enrolled_at IS NULL));
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM submission_enrollment s
               JOIN submission_face_group g USING(group_id)
               LEFT JOIN subject_example e ON e.submission_group_id=s.group_id
               WHERE e.example_id IS NULL OR e.source_id<>s.source_id
                  OR g.run_id<>s.run_id OR g.embedding_model_version<>s.embedding_model_version) THEN
        RAISE EXCEPTION 'Enrollment origin evidence must be reconciled before migration';
    END IF;
END $$;
UPDATE submission_face_group g
SET enrollment_decision=s.decision,enrolled_at=s.created_at
FROM submission_enrollment s WHERE s.group_id=g.group_id;
DROP TABLE submission_enrollment;
