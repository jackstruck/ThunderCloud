ALTER TABLE source_asset ADD COLUMN storage_kind text NOT NULL DEFAULT 'none'
    CHECK(storage_kind IN ('none','managed','archive'));
UPDATE source_asset SET storage_kind='managed' WHERE object_name IS NOT NULL;
DROP INDEX retained_source_digest_idx;
CREATE UNIQUE INDEX retained_source_digest_idx ON source_asset(source_sha256)
    WHERE storage_kind='managed' AND deleted_at IS NULL;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM source_asset WHERE storage_kind='none' AND external_source_ref LIKE 'gs://%'
        AND (external_source_ref !~ '^gs://[^/]+/.+$' OR
             COALESCE(metadata->>'generation','') !~ '^[1-9][0-9]*$')) THEN
        RAISE EXCEPTION 'Archive sources require a complete storage URI and positive exact generation';
    END IF;
END $$;
UPDATE source_asset SET storage_kind='archive',
    object_bucket=split_part(substr(external_source_ref,6),'/',1),
    object_name=substr(external_source_ref,6+strpos(substr(external_source_ref,6),'/')),
    object_generation=(metadata->>'generation')::bigint,
    metadata=metadata-'generation'
WHERE storage_kind='none' AND external_source_ref LIKE 'gs://%';
ALTER TABLE source_asset ADD CONSTRAINT source_storage_complete CHECK (
    (storage_kind='none' AND object_name IS NULL AND object_generation IS NULL)
    OR (storage_kind IN ('managed','archive') AND object_name IS NOT NULL AND object_name<>''
        AND object_bucket IS NOT NULL AND object_generation IS NOT NULL AND object_generation>0));
