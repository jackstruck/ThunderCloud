ALTER TABLE subject ADD COLUMN matching_excluded boolean NOT NULL DEFAULT false;
ALTER TABLE subject_example
    ADD COLUMN disabled_reason text CHECK(disabled_reason IN ('not_face','unusable_face')),
    ADD COLUMN disabled_at timestamptz,
    ADD COLUMN disabled_by text,
    ADD CONSTRAINT example_disabling_complete CHECK (
        (disabled_reason IS NULL AND disabled_at IS NULL AND disabled_by IS NULL)
        OR (disabled_reason IS NOT NULL AND disabled_at IS NOT NULL AND disabled_by IS NOT NULL));
ALTER TABLE subject_change_event DROP CONSTRAINT subject_change_event_action_check;
ALTER TABLE subject_change_event ADD CONSTRAINT subject_change_event_action_check
    CHECK(action IN ('edit','combine','move','dismiss_suggestion','restore_suggestion',
                     'set_matching_exclusion','set_example_status'));
