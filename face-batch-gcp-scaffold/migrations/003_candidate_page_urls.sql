-- Snapshot public source-page attribution with candidate results.
ALTER TABLE run_candidate
    ADD COLUMN IF NOT EXISTS page_urls jsonb NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE run_candidate DROP CONSTRAINT IF EXISTS run_candidate_page_urls_array;
ALTER TABLE run_candidate
    ADD CONSTRAINT run_candidate_page_urls_array
    CHECK (jsonb_typeof(page_urls) = 'array') NOT VALID;

ALTER TABLE run_candidate VALIDATE CONSTRAINT run_candidate_page_urls_array;

-- Existing retained results gain a one-time snapshot from their subjects' lineage.
UPDATE run_candidate candidate
SET page_urls = COALESCE((
    SELECT jsonb_agg(attribution.page_url ORDER BY attribution.page_url)
    FROM (
        SELECT DISTINCT COALESCE(asset.metadata->>'page_url', asset.metadata->>'luluvid_url') AS page_url
        FROM source_asset asset
        WHERE asset.source_id IN (
            SELECT source_id FROM face_track WHERE subject_id = candidate.subject_id
            UNION
            SELECT source_id FROM submission_enrollment WHERE subject_id = candidate.subject_id
        )
    ) attribution
    WHERE attribution.page_url IS NOT NULL
), '[]'::jsonb);
