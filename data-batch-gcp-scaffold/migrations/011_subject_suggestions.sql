-- Shared review state uses the existing subject row versions.
CREATE TABLE subject_suggestion_dismissal (
    subject_low uuid NOT NULL REFERENCES subject(subject_id),
    subject_high uuid NOT NULL REFERENCES subject(subject_id),
    low_version bigint NOT NULL CHECK (low_version > 0),
    high_version bigint NOT NULL CHECK (high_version > 0),
    dismissed_by text NOT NULL,
    dismissed_at timestamptz NOT NULL DEFAULT now(),
    restored_by text,
    restored_at timestamptz,
    PRIMARY KEY (subject_low, subject_high),
    CHECK (subject_low < subject_high),
    CHECK ((restored_by IS NULL) = (restored_at IS NULL))
);
ALTER TABLE subject_change_event DROP CONSTRAINT subject_change_event_action_check;
ALTER TABLE subject_change_event ADD CONSTRAINT subject_change_event_action_check
    CHECK (action IN ('edit', 'combine', 'move', 'dismiss_suggestion', 'restore_suggestion'));
-- Existing snapshots intentionally retain an unknown compared version.
ALTER TABLE run_candidate ADD COLUMN compared_subject_version bigint
    CHECK (compared_subject_version > 0);
