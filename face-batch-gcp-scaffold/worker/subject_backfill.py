"""Resumable example maintenance and galleries from retained enrollment sources."""

from __future__ import annotations

import json
import os
from pathlib import Path

from .gallery_backfill import (
    ExistingTrack,
    GalleryBackfill,
    RegeneratedTrack,
    _cosine,
)
from .subject_management import SubjectManagement, reconcile_gallery


class SubjectGalleryRepository:
    def __init__(self, database, bucket):
        self.database = database
        self.bucket = bucket
        self.subjects = SubjectManagement(database)

    def active_ids(self, subject_id):
        with self.subjects.transaction() as cursor:
            cursor.execute(
                """SELECT r.representative_id FROM subject_representative_face r
                   JOIN subject_example e USING(example_id)
                   WHERE e.subject_id=%s AND r.active
                   ORDER BY r.quality_score DESC NULLS LAST,r.representative_id LIMIT 5""",
                (subject_id,),
            )
            return [str(row[0]) for row in cursor.fetchall()]

    def queue_fallback(self, existing, reason):
        with self.subjects.transaction(write=True) as cursor:
            cursor.execute(
                """INSERT INTO gallery_fallback_source(source_id,source_uri,reason)
                   VALUES (%s,%s,%s) ON CONFLICT (source_id) DO NOTHING""",
                (existing.source_id, existing.source_uri, reason),
            )

    def candidates(self, limit, model_version):
        with self.subjects.transaction() as cursor:
            cursor.execute(
                """WITH ranked AS (
                  SELECT e.*, g.local_group_id,sa.content_type, e.model_version AS effective_model_version, sa.source_sha256,
                    'gs://' || sa.object_bucket || '/' || sa.object_name AS source_uri,
                    sa.object_generation AS generation,
                    row_number() OVER (PARTITION BY e.subject_id,e.source_id
                      ORDER BY e.quality_score DESC NULLS LAST,e.example_id) AS rank
                  FROM subject_example e JOIN source_asset sa USING(source_id)
                  JOIN subject s USING(subject_id)
                  LEFT JOIN submission_face_group g ON g.group_id=e.submission_group_id
                  WHERE e.model_version=%s AND s.merged_into_subject_id IS NULL
                    AND sa.deleted_at IS NULL
                    AND sa.storage_kind IN ('managed','archive')
                    AND (SELECT count(*) FROM subject_representative_face r JOIN subject_example owner USING(example_id)
                         WHERE owner.subject_id=e.subject_id AND r.active)<5
                    AND NOT EXISTS (SELECT 1 FROM subject_representative_face r JOIN subject_example owner USING(example_id)
                         WHERE owner.subject_id=e.subject_id AND owner.source_id=e.source_id AND r.active)
                    AND ((%s AND e.submission_group_id IS NOT NULL) OR NOT EXISTS (SELECT 1 FROM gallery_fallback_source f WHERE f.source_id=e.source_id))
                ), candidates AS (SELECT * FROM ranked WHERE rank=1),
                selected AS (SELECT DISTINCT subject_id FROM candidates ORDER BY subject_id LIMIT %s)
                SELECT c.subject_id,c.source_id,c.example_id,c.source_uri,c.generation,
                  c.source_sha256,c.start_ms,c.end_ms,c.embedding::text,c.effective_model_version,
                  COALESCE(c.quality_score,0),c.local_group_id,c.content_type
                FROM candidates c JOIN selected USING(subject_id)
                ORDER BY c.subject_id,c.quality_score DESC NULLS LAST,c.source_id""",
                (
                    model_version,
                    os.getenv(
                        "FACE_BACKFILL_RETRY_INTERACTIVE_FALLBACK", "false"
                    ).lower()
                    == "true",
                    limit,
                ),
            )
            return [
                ExistingTrack(
                    str(r[0]),
                    str(r[1]),
                    str(r[2]),
                    r[3],
                    int(r[4]),
                    r[5],
                    int(r[6] or 0),
                    int(r[7] or 0),
                    json.loads(r[8]),
                    r[9],
                    float(r[10]),
                    None if r[11] is None else int(r[11]),
                    r[12],
                )
                for r in cursor.fetchall()
            ]

    def stage(self, existing, regenerated, representative_id, object_name, generation):
        # Preserve cleanup evidence if publication fails after the storage upload.
        with self.subjects.transaction(write=True) as cursor:
            cursor.execute(
                """INSERT INTO gallery_cleanup_object(representative_id,object_name,object_generation)
                   VALUES (%s,%s,%s) ON CONFLICT (object_name,object_generation) DO NOTHING""",
                (representative_id, object_name, generation),
            )
        with self.subjects.transaction(write=True) as cursor:
            cursor.execute(
                "SELECT subject_id FROM subject_example WHERE example_id=%s FOR UPDATE",
                (existing.track_id,),
            )
            (subject_id,) = cursor.fetchone()
            cursor.execute(
                """SELECT c.state FROM gallery_cleanup_object c
                   JOIN subject_representative_face r
                     ON r.object_name=c.object_name AND r.object_generation=c.object_generation
                   WHERE r.representative_id=%s FOR UPDATE OF c""",
                (representative_id,),
            )
            if any(row[0] == "leased" for row in cursor.fetchall()):
                raise RuntimeError(
                    "Gallery cleanup is in progress; retry publication afterward."
                )
            cursor.execute(
                """INSERT INTO subject_representative_face
                   (representative_id,example_id,
                    object_name,object_generation,content_type,source_timestamp_ms,quality_score,active)
                   VALUES (%s,%s,%s,%s,'image/jpeg',%s,%s,true)
                   ON CONFLICT (representative_id) DO UPDATE SET
                     active=true,retired_at=NULL
                   RETURNING object_name,object_generation""",
                (
                    representative_id,
                    existing.track_id,
                    object_name,
                    generation,
                    regenerated.start_ms,
                    regenerated.quality,
                ),
            )
            published_name, published_generation = cursor.fetchone()
            cursor.execute(
                "DELETE FROM gallery_cleanup_object WHERE object_name=%s AND object_generation=%s",
                (published_name, published_generation),
            )
            reconcile_gallery(cursor, [subject_id])

    def publish(self, subject_id, representative_ids):
        # Each crop is published atomically with its current example owner in stage().
        with self.subjects.transaction(write=True) as cursor:
            cursor.execute(
                """DELETE FROM gallery_fallback_source f WHERE f.source_id IN
                     (SELECT e.source_id FROM subject_representative_face r JOIN subject_example e USING(example_id) WHERE r.representative_id=ANY(%s::uuid[]) AND r.active)
                   AND NOT EXISTS (SELECT 1 FROM subject_example e WHERE e.source_id=f.source_id
                     AND (SELECT count(*) FROM subject_representative_face r JOIN subject_example owner USING(example_id) WHERE owner.subject_id=e.subject_id AND r.active)<5
                     AND NOT EXISTS (SELECT 1 FROM subject_representative_face r JOIN subject_example owner USING(example_id)
                       WHERE owner.subject_id=e.subject_id AND owner.source_id=e.source_id AND r.active))""",
                (representative_ids,),
            )


class SubjectGalleryBackfill(GalleryBackfill):
    def regenerate(self, path, tracks):
        interactive = [
            t
            for t in tracks
            if t.local_track_id is not None and t.content_type == "video/mp4"
        ]
        if not interactive:
            return super().regenerate(path, tracks)
        import hashlib

        import av

        from .probe_service import LocalMedia, video_faces

        data = Path(path).read_bytes()
        if len(data) > self.settings.max_probe_video_bytes:
            raise ValueError(
                "Retained interactive source exceeds its original byte limit."
            )
        faces, crops = video_faces(
            LocalMedia("video/mp4", data, hashlib.sha256(data).hexdigest()),
            self.settings,
            self.detector,
            self.embedder,
        )
        regenerated = {}
        for track in interactive:
            matches = [
                f
                for f in faces
                if f["local_face_id"] == track.local_track_id
                and f["start_ms"] == track.start_ms
                and f["end_ms"] == track.end_ms
                and _cosine(f["embedding"], track.embedding) >= 0.99999
            ]
            if len(matches) != 1:
                continue
            face = matches[0]
            prefix = f"track-{track.local_track_id:06d}/"
            crop = next((c for c in crops if c.relative_path.startswith(prefix)), None)
            if crop is not None:
                regenerated[track.track_id] = RegeneratedTrack(
                    track.local_track_id,
                    track.start_ms,
                    track.end_ms,
                    face["embedding"],
                    face["max_quality"] or 0,
                    crop.content,
                )
        remaining = [t for t in tracks if t.track_id not in regenerated]
        if remaining:
            fallback, _, _ = super().regenerate(path, remaining)
            regenerated.update(fallback)
        with av.open(path) as container:
            duration = int((container.duration or 0) / av.time_base * 1000)
        return regenerated, duration, duration
