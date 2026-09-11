-- Operator archives use the same run and operation framework as browser inputs.
ALTER TABLE media_run
    ADD COLUMN selection_policy text NOT NULL DEFAULT 'manual'
        CHECK (selection_policy IN ('manual','all_tracks')),
    ADD COLUMN archive_bucket text,
    ADD COLUMN archive_object_name text,
    ADD COLUMN archive_object_generation bigint,
    ADD COLUMN archive_sha256 char(64);
ALTER TABLE media_run DROP CONSTRAINT media_run_source_kind_check;
ALTER TABLE media_run ADD CONSTRAINT media_run_source_kind_check
    CHECK (source_kind IN ('url','upload','archive'));
ALTER TABLE media_run DROP CONSTRAINT media_run_check;
ALTER TABLE media_run ADD CONSTRAINT media_run_url_attribution_check
    CHECK (source_kind <> 'url' OR source_page_url IS NOT NULL);
ALTER TABLE media_run ADD CONSTRAINT media_run_archive_reference_check CHECK (
    (source_kind='archive' AND archive_bucket IS NOT NULL AND archive_bucket<>''
      AND archive_object_name IS NOT NULL AND archive_object_name<>''
      AND archive_object_generation IS NOT NULL AND archive_object_generation>0
      AND archive_sha256 IS NOT NULL AND archive_sha256 ~ '^[0-9a-f]{64}$')
    OR (source_kind<>'archive' AND archive_bucket IS NULL AND archive_object_name IS NULL
      AND archive_object_generation IS NULL AND archive_sha256 IS NULL)
);
