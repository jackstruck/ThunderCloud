-- Durable handoff separates profile acquisition from processing admission.
ALTER TABLE media_run ADD COLUMN workload text NOT NULL DEFAULT 'interactive'
    CHECK (workload IN ('interactive','batch'));

CREATE TABLE import_queue (
    queue_id uuid PRIMARY KEY,
    batch_id uuid NOT NULL,
    principal text NOT NULL,
    idempotency_key text NOT NULL,
    request_fingerprint text NOT NULL,
    payload jsonb NOT NULL,
    state text NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','leased','submitted','failed')),
    run_id uuid REFERENCES media_run(run_id),
    attempts integer NOT NULL DEFAULT 0,
    lease_owner uuid,
    lease_expires_at timestamptz,
    available_at timestamptz NOT NULL DEFAULT now(),
    last_error_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    submitted_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (principal,idempotency_key),
    CHECK ((state='submitted') = (run_id IS NOT NULL))
);
CREATE INDEX import_queue_claim_idx ON import_queue(available_at,created_at,queue_id)
    WHERE state IN ('pending','leased');
CREATE INDEX import_queue_batch_idx ON import_queue(batch_id,created_at,queue_id);
CREATE TABLE import_dispatch_lease (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    lease_owner uuid,
    lease_expires_at timestamptz
);
CREATE INDEX media_run_processing_capacity_idx ON media_run(workload,state)
    WHERE state IN ('fetching','queued','detecting','matching','enrolling');
CREATE INDEX media_run_archive_identity_idx
    ON media_run(archive_bucket,archive_object_name,archive_object_generation,handling_policy,created_at,run_id)
    WHERE source_kind='archive';
