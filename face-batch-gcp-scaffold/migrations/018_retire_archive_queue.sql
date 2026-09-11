-- Cutover must drain the predecessor queue before retiring its storage.
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM processing_work_item w JOIN processing_rollout r USING(rollout_id)
        WHERE w.state IN ('pending','leased','retry')
          AND NOT (w.state='retry' AND COALESCE(w.last_error_code,'')='OPERATOR_CANCELLED' AND r.status='cancelled')) THEN
        RAISE EXCEPTION 'Archive work must be drained before queue retirement';
    END IF;
END $$;
-- Preserve exact source provenance before removing the queue. Some early sources
-- omitted generation metadata even though a later successful run pinned it.
DO $$ BEGIN
    IF EXISTS (
        SELECT 1 FROM source_asset s JOIN processing_work_item w
          ON w.source_uri=s.external_source_ref AND w.source_sha256=s.source_sha256
        WHERE s.object_name IS NULL AND s.external_source_ref LIKE 'gs://%'
          AND s.metadata->>'generation' IS NULL AND w.state='succeeded'
          AND w.source_generation>0
        GROUP BY s.source_id HAVING count(DISTINCT w.source_generation)>1
    ) THEN
        RAISE EXCEPTION 'Archive generation evidence is ambiguous';
    END IF;
END $$;
UPDATE source_asset s SET metadata=jsonb_set(s.metadata,'{generation}',to_jsonb(e.generation))
FROM (
    SELECT source_uri,source_sha256,min(source_generation) AS generation
    FROM processing_work_item WHERE state='succeeded' AND source_generation>0
    GROUP BY source_uri,source_sha256 HAVING count(DISTINCT source_generation)=1
) e
WHERE s.external_source_ref=e.source_uri AND s.source_sha256=e.source_sha256
  AND s.object_name IS NULL AND s.external_source_ref LIKE 'gs://%'
  AND s.metadata->>'generation' IS NULL;
DROP TABLE processing_work_item;
DROP TABLE processing_rollout;
