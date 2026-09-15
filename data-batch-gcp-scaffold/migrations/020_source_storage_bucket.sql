ALTER TABLE source_asset ADD COLUMN object_bucket text CHECK(object_bucket IS NULL OR object_bucket<>'');
-- Existing managed objects require an explicit deployment-specific bucket.
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM source_asset WHERE object_name IS NOT NULL)
       AND NULLIF(current_setting('thundercloud.source_bucket',true),'') IS NULL THEN
        RAISE EXCEPTION 'Set thundercloud.source_bucket to the reviewed retained-storage bucket before migration';
    END IF;
END $$;
UPDATE source_asset SET object_bucket=current_setting('thundercloud.source_bucket',true)
WHERE object_name IS NOT NULL;
ALTER TABLE source_asset ADD CONSTRAINT source_bucket_matches_object
    CHECK ((object_name IS NULL)=(object_bucket IS NULL));
