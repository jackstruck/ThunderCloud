CREATE TABLE IF NOT EXISTS gallery_fallback_source (
    source_id uuid PRIMARY KEY REFERENCES source_asset(source_id) ON DELETE CASCADE,
    source_uri text NOT NULL,
    reason text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
