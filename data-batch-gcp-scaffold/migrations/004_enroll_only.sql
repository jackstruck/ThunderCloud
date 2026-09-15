-- Derived-only enrollment: preserve provenance and biometric records without retaining media.
ALTER TABLE media_run DROP CONSTRAINT IF EXISTS media_run_handling_policy_check;
ALTER TABLE media_run ADD CONSTRAINT media_run_handling_policy_check
    CHECK (handling_policy IN ('search_then_discard', 'retain_and_enroll', 'enroll_only'));
