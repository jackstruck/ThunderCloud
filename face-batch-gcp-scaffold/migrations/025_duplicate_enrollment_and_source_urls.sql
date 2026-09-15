-- URL provenance is shared by exact content, including sources enrolled without retention.
CREATE TABLE source_url (
    source_sha256 text NOT NULL,
    page_url text NOT NULL,
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source_sha256,page_url)
);
INSERT INTO source_url(source_sha256,page_url)
SELECT source_sha256,source_page_url FROM source_asset WHERE source_page_url IS NOT NULL
UNION
SELECT source_sha256,source_page_url FROM media_run
WHERE enrollment_completed_at IS NOT NULL AND source_sha256 IS NOT NULL AND source_page_url IS NOT NULL
ON CONFLICT DO NOTHING;
CREATE VIEW source_page_link AS
SELECT source_id,source_page_url AS page_url FROM source_asset WHERE source_page_url IS NOT NULL
UNION
SELECT s.source_id,u.page_url FROM source_asset s JOIN source_url u USING(source_sha256);
ALTER TABLE media_run ADD COLUMN enrollment_reused boolean NOT NULL DEFAULT false;
CREATE INDEX source_asset_content_lookup ON source_asset(source_sha256,created_at,source_id);
