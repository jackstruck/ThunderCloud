ALTER TABLE run_cleanup_object ADD COLUMN object_bucket text;
UPDATE run_cleanup_object c SET object_bucket=COALESCE(
    (SELECT DISTINCT s.object_bucket FROM source_asset s
     WHERE s.object_name=c.object_name AND s.object_generation=c.object_generation),
    NULLIF(current_setting('thundercloud.source_bucket',true),''))
WHERE c.object_name LIKE 'training-media/%';
ALTER TABLE run_cleanup_object ADD CONSTRAINT retained_cleanup_bucket_required
    CHECK(object_name NOT LIKE 'training-media/%' OR (object_bucket IS NOT NULL AND object_bucket<>''));
