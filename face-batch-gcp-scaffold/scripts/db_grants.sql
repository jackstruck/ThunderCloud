-- Shared by fresh setup and upgrades. The runner sets thundercloud.app_user
-- transaction-locally; no role/password interpolation is used by the caller.
DO $$
DECLARE
    principal text := current_setting('thundercloud.app_user');
BEGIN
    IF principal = '' THEN RAISE EXCEPTION 'Application principal is required'; END IF;
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO %I', current_database(), principal);
    EXECUTE format('GRANT USAGE ON SCHEMA public TO %I', principal);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON identity, subject,
        source_asset, processing_job, face_track,
        media_run, submission_face_group, run_candidate,
        run_operation, subject_representative_face, run_cleanup_object,
        gallery_cleanup_object, gallery_fallback_source,
        subject_example, enrollment_assignment, enrollment_assignment_member,
        subject_change_event, subject_suggestion_dismissal TO %I', principal);
    EXECUTE format('REVOKE ALL ON verified_embedding_model, platform_schema_migration FROM %I', principal);
    EXECUTE format('GRANT SELECT ON verified_embedding_model TO %I', principal);
END $$;
