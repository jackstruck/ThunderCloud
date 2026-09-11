-- Historical threshold labels remain evidence; new enrollment has no comparison gate.
ALTER TABLE submission_enrollment ALTER COLUMN threshold_version DROP NOT NULL;
