-- Origin tracks/jobs retain their reported labels and existing verification evidence.
-- Only the administrative migration can normalize immutable example labels.
ALTER TABLE subject_example DISABLE TRIGGER protect_subject_example_origin;
UPDATE subject_example e SET model_version=v.verified_model_version
FROM face_track t JOIN verified_embedding_model v ON v.processing_job_id=t.processing_job_id
WHERE e.face_track_id=t.track_id AND e.model_version=v.reported_model_version;
ALTER TABLE subject_example ENABLE TRIGGER protect_subject_example_origin;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM subject_example e JOIN subject s USING(subject_id)
               WHERE e.model_version<>s.model_version) THEN
        RAISE EXCEPTION 'Normalized example models differ from their subject representations';
    END IF;
END $$;
