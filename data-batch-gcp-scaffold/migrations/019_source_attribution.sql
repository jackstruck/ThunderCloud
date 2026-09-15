ALTER TABLE source_asset ADD COLUMN source_page_url text;
UPDATE source_asset SET source_page_url=COALESCE(metadata->>'page_url',metadata->>'luluvid_url'),
    metadata=metadata-'page_url'-'luluvid_url';
