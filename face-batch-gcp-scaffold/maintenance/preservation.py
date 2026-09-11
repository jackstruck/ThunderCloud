"""Logical preservation receipts; never publish raw embeddings or member maps."""

from __future__ import annotations

import hashlib
import json
import math
import time

from maintenance.migrations import MigrationError

# Versioned, reviewed projections; no caller-supplied SQL or omitted mismatch flags.
PROJECTIONS = {
    "examples": """SELECT e.example_id,e.subject_id,e.source_id,e.face_track_id,e.submission_group_id,
        e.embedding::text,COALESCE(v.verified_model_version,e.model_version) AS model_version,
        e.start_ms,e.end_ms,e.quality_score,e.created_at
        FROM subject_example e LEFT JOIN face_track t ON t.track_id=e.face_track_id
        LEFT JOIN verified_embedding_model v ON v.processing_job_id=t.processing_job_id
          AND v.reported_model_version=e.model_version ORDER BY e.example_id""",
    "subjects": """SELECT subject_id,identity_id,canonical_embedding::text,model_version,
        sample_count,metadata,created_at,updated_at,row_version,merged_into_subject_id
        FROM subject ORDER BY subject_id""",
    "identities": "SELECT * FROM identity ORDER BY identity_id",
    "sources": """SELECT (to_jsonb(s)-'metadata'-'source_page_url'-'object_bucket'-'object_name'-'object_generation'-'storage_kind') || jsonb_build_object(
        'metadata',CASE WHEN object_name IS NULL AND external_source_ref LIKE 'gs://%'
            THEN metadata-'page_url'-'luluvid_url'-'generation' ELSE metadata-'page_url'-'luluvid_url' END,
        'storage_kind',COALESCE(to_jsonb(s)->>'storage_kind',CASE WHEN object_name IS NOT NULL THEN 'managed'
            WHEN external_source_ref LIKE 'gs://%' THEN 'archive' ELSE 'none' END),
        'object_bucket',CASE WHEN to_jsonb(s) ? 'storage_kind' THEN to_jsonb(s)->>'object_bucket'
            WHEN object_name IS NOT NULL THEN COALESCE(to_jsonb(s)->>'object_bucket',NULLIF(current_setting('thundercloud.source_bucket',true),''))
            WHEN external_source_ref LIKE 'gs://%' THEN split_part(substr(external_source_ref,6),'/',1) END,
        'object_name',CASE WHEN NOT(to_jsonb(s) ? 'storage_kind') AND object_name IS NULL AND external_source_ref LIKE 'gs://%'
            THEN substr(external_source_ref,6+strpos(substr(external_source_ref,6),'/')) ELSE object_name END,
        'object_generation',CASE WHEN NOT(to_jsonb(s) ? 'storage_kind') AND object_name IS NULL AND external_source_ref LIKE 'gs://%'
            THEN (metadata->>'generation')::bigint ELSE object_generation END,
        'source_page_url',COALESCE(to_jsonb(s)->>'source_page_url',metadata->>'page_url',metadata->>'luluvid_url')) AS source
        FROM source_asset s ORDER BY source_id""",
    "gallery": """SELECT g.representative_id,g.example_id,e.subject_id,e.source_id,
        g.object_name,g.object_generation,g.content_type,g.source_timestamp_ms,
        g.quality_score,g.active,g.created_at,g.retired_at
        FROM subject_representative_face g LEFT JOIN subject_example e USING(example_id)
        ORDER BY g.representative_id""",
    "track_evidence": """SELECT track_id,source_id,processing_job_id,local_track_id,
        start_ms,end_ms,aggregate_embedding::text,model_version,observation_count,
        embedded_count FROM face_track ORDER BY track_id""",
    "group_evidence": """SELECT group_id,run_id,local_group_id,bbox,start_ms,end_ms,
        quality_summary,detector_version,embedding_model_version,aggregate_embedding::text,
        created_at FROM submission_face_group ORDER BY group_id""",
    "model_evidence": "SELECT * FROM verified_embedding_model ORDER BY processing_job_id",
    "assignment_groups": "SELECT assignment_id,run_id,created_at FROM enrollment_assignment ORDER BY assignment_id",
    "assignment_members": "SELECT * FROM enrollment_assignment_member ORDER BY group_id",
    "correction_audit": "SELECT * FROM subject_change_event ORDER BY operation_id",
    "result_snapshots": """SELECT run_id,group_id,subject_id,rank,similarity,
        display_name_snapshot,source_count,observation_count,created_at,page_urls,
        to_jsonb(candidate)->'compared_subject_version' AS compared_subject_version
        FROM run_candidate candidate ORDER BY run_id,group_id,subject_id""",
}
INVARIANTS = {
    "merged_subject_members": """SELECT count(*) FROM subject_example e JOIN subject s USING(subject_id)
        WHERE s.merged_into_subject_id IS NOT NULL""",
    "subject_counts": """SELECT count(*) FROM subject s WHERE sample_count <>
        (SELECT count(*) FROM subject_example e WHERE e.subject_id=s.subject_id)""",
    "empty_representations": """SELECT count(*) FROM subject WHERE
        (sample_count=0) <> (canonical_embedding IS NULL)""",
    "model_provenance": """SELECT count(*) FROM subject_example e JOIN subject s USING(subject_id)
        LEFT JOIN face_track ft ON ft.track_id=e.face_track_id
        LEFT JOIN verified_embedding_model v ON v.processing_job_id=ft.processing_job_id
          AND v.reported_model_version=e.model_version
        WHERE COALESCE(v.verified_model_version,e.model_version)<>s.model_version""",
    "track_origins": """SELECT count(*) FROM subject_example e JOIN face_track t ON t.track_id=e.face_track_id
        LEFT JOIN verified_embedding_model v ON v.processing_job_id=t.processing_job_id
          AND v.reported_model_version=t.model_version
        WHERE e.source_id<>t.source_id OR e.embedding::text<>t.aggregate_embedding::text
        OR e.model_version NOT IN (t.model_version,COALESCE(v.verified_model_version,t.model_version)) OR e.start_ms IS DISTINCT FROM t.start_ms
        OR e.end_ms IS DISTINCT FROM t.end_ms""",
    "group_origins": """SELECT count(*) FROM subject_example e
        JOIN submission_face_group g ON g.group_id=e.submission_group_id
        WHERE e.embedding::text<>g.aggregate_embedding::text
        OR e.model_version<>g.embedding_model_version OR e.start_ms IS DISTINCT FROM g.start_ms
        OR e.end_ms IS DISTINCT FROM g.end_ms""",
    "active_gallery_links": """SELECT count(*) FROM subject_representative_face g
        LEFT JOIN subject_example e USING(example_id)
        WHERE g.active AND (e.example_id IS NULL OR g.object_name='' OR g.object_generation<=0)""",
    "gallery_ownership": """SELECT count(*) FROM subject_representative_face g
        JOIN subject_example e USING(example_id)
        WHERE g.active AND to_jsonb(g) ? 'subject_id'
          AND ((to_jsonb(g)->>'subject_id')::uuid<>e.subject_id
            OR (to_jsonb(g)->>'source_id')::uuid IS DISTINCT FROM e.source_id)""",
    "retained_generations": """SELECT count(*) FROM source_asset WHERE object_name IS NOT NULL
        AND (object_generation IS NULL OR object_generation<=0 OR object_name='')""",
}


def stream(cursor, query, heartbeat=None):
    """A server cursor keeps large embedding receipts bounded in client memory."""
    cursor.execute("DECLARE preservation_rows NO SCROLL CURSOR FOR " + query)
    last_heartbeat = time.monotonic()
    try:
        while True:
            cursor.execute("FETCH FORWARD 256 FROM preservation_rows")
            if heartbeat and time.monotonic() - last_heartbeat >= 15:
                heartbeat()
                last_heartbeat = time.monotonic()
            rows = cursor.fetchall()
            if not rows:
                return
            yield from rows
    finally:
        cursor.execute("CLOSE preservation_rows")


def row_digest(cursor, query, heartbeat=None):
    fingerprint, count = hashlib.sha256(), 0
    for (value,) in stream(
        cursor,
        "SELECT row_to_json(record)::text FROM (" + query + ") record",
        heartbeat,
    ):
        raw = value.encode()
        fingerprint.update(len(raw).to_bytes(8, "big"))
        fingerprint.update(raw)
        count += 1
    return {"rows": count, "sha256": fingerprint.hexdigest()}


def representation_errors(cursor, heartbeat=None):
    errors, previous, count, total, actual = 0, None, 0, [0.0] * 512, None

    def mismatch():
        if not count or actual is None:
            return 1
        norm = math.sqrt(sum(v * v for v in total))
        if not math.isfinite(norm) or norm <= 1e-12:
            return 1
        recorded = json.loads(actual)
        return int(
            len(recorded) != 512
            or any(
                not math.isfinite(b) or abs(a / norm - b) > 1e-5
                for a, b in zip(total, recorded, strict=False)
            )
        )

    query = """SELECT e.subject_id,e.embedding::text,s.canonical_embedding::text
        FROM subject_example e JOIN subject s USING(subject_id) ORDER BY e.subject_id,e.example_id"""
    for subject, embedding, canonical in stream(cursor, query, heartbeat):
        if subject != previous:
            if previous is not None:
                errors += mismatch()
            previous, total, count, actual = subject, [0.0] * 512, 0, canonical
        values = json.loads(embedding)
        norm = math.sqrt(sum(v * v for v in values))
        if len(values) != 512 or not math.isfinite(norm) or norm <= 1e-12:
            errors += 1
            continue
        total = [a + b / norm for a, b in zip(total, values, strict=True)]
        count += 1
    if previous is not None:
        errors += mismatch()
    return errors


def capture(connection, heartbeat=None, source_bucket=None):
    """Caller supplies an idle connection; no data mutation is performed."""
    cursor = connection.cursor()
    try:
        cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        cursor.execute("SET LOCAL search_path TO public,pg_catalog")
        cursor.execute("SET LOCAL timezone TO 'UTC'")
        cursor.execute(
            "SELECT set_config('thundercloud.source_bucket',%s,true)",
            (source_bucket or "",),
        )
        cursor.execute("""SELECT count(*) FROM source_asset s WHERE object_name IS NOT NULL
            AND CASE WHEN to_jsonb(s) ? 'object_bucket' THEN to_jsonb(s)->>'object_bucket'
              ELSE NULLIF(current_setting('thundercloud.source_bucket',true),'') END IS NULL""")
        if cursor.fetchone()[0]:
            raise MigrationError(
                "Explicit source bucket is required to verify retained storage"
            )
        projections = dict(PROJECTIONS)
        cursor.execute("SELECT to_regclass('public.processing_work_item')")
        if cursor.fetchone()[0] is not None:
            # Match migration 018's unique successful URI+digest evidence before
            # the queue is retired; no current-object lookup or guessed version.
            projections["sources"] = projections["sources"].replace(
                "(metadata->>'generation')::bigint",
                """COALESCE((metadata->>'generation')::bigint,
                    (SELECT min(w.source_generation) FROM processing_work_item w
                     WHERE w.source_uri=s.external_source_ref
                       AND w.source_sha256=s.source_sha256 AND w.state='succeeded'
                       AND w.source_generation>0
                     HAVING count(DISTINCT w.source_generation)=1))""",
            )
        receipts = {
            name: row_digest(cursor, sql, heartbeat)
            for name, sql in projections.items()
        }
        cursor.execute("SELECT to_regclass('public.subject_suggestion_dismissal')")
        if cursor.fetchone()[0] is None:
            receipts["suggestion_dismissals"] = {
                "rows": 0,
                "sha256": hashlib.sha256().hexdigest(),
            }
        else:
            receipts["suggestion_dismissals"] = row_digest(
                cursor,
                "SELECT * FROM subject_suggestion_dismissal ORDER BY subject_low,subject_high",
                heartbeat,
            )
        cursor.execute("SELECT to_regclass('public.submission_enrollment')")
        if cursor.fetchone()[0] is not None:
            enrollment = """SELECT group_id,run_id,source_id,decision,created_at
                FROM submission_enrollment ORDER BY group_id"""
        else:
            enrollment = """SELECT g.group_id,g.run_id,e.source_id,
                g.enrollment_decision AS decision,g.enrolled_at AS created_at
                FROM submission_face_group g JOIN subject_example e ON e.submission_group_id=g.group_id
                WHERE g.enrolled_at IS NOT NULL ORDER BY g.group_id"""
        receipts["enrollment"] = row_digest(cursor, enrollment, heartbeat)
        checks = {}
        for name, sql in INVARIANTS.items():
            cursor.execute(sql)
            checks[name] = cursor.fetchone()[0]
        checks["representations"] = representation_errors(cursor, heartbeat)
        return {"format": 1, "records": receipts, "violations": checks}
    finally:
        connection.rollback()
        cursor.close()


def verify(before, after):
    changed = sorted(
        key
        for key in before["records"]
        if before["records"][key] != after["records"].get(key)
    )
    invalid = sorted(key for key, value in after["violations"].items() if value)
    if changed or invalid:
        raise MigrationError(
            "Preservation check failed: " + ", ".join(changed + invalid)
        )
    return {"preserved": sorted(before["records"]), "violations": after["violations"]}
