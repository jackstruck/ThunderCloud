from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass(frozen=True)
class ExistingTrack:
    subject_id: str
    source_id: str
    track_id: str
    source_uri: str
    source_generation: int
    source_sha256: str
    start_ms: int
    end_ms: int
    embedding: Any
    model_version: str
    quality: float


@dataclass(frozen=True)
class RegeneratedTrack:
    local_id: int
    start_ms: int
    end_ms: int
    embedding: Any
    quality: float
    crop_jpeg: bytes


def _cosine(left, right) -> float:
    left_array, right_array = np.asarray(left), np.asarray(right)
    denominator = np.linalg.norm(left_array) * np.linalg.norm(right_array)
    return (
        0.0
        if denominator == 0
        else float(np.dot(left_array, right_array) / denominator)
    )


def _overlap(left_start: int, left_end: int, right_start: int, right_end: int) -> float:
    intersection = max(0, min(left_end, right_end) - max(left_start, right_start))
    union = max(left_end, right_end) - min(left_start, right_start)
    return 0.0 if union <= 0 else intersection / union


def associate_track(
    existing: ExistingTrack,
    regenerated: list[RegeneratedTrack],
    *,
    minimum_overlap: float = 0.35,
    minimum_similarity: float = 0.65,
    ambiguity_margin: float = 0.05,
) -> RegeneratedTrack | None:
    scored = []
    for candidate in regenerated:
        overlap = _overlap(
            existing.start_ms, existing.end_ms, candidate.start_ms, candidate.end_ms
        )
        similarity = _cosine(existing.embedding, candidate.embedding)
        if overlap >= minimum_overlap and similarity >= minimum_similarity:
            scored.append((overlap * similarity, similarity, overlap, candidate))
    scored.sort(key=lambda item: (-item[0], -item[1], -item[2], item[3].local_id))
    if not scored:
        return None
    if len(scored) > 1 and scored[0][0] - scored[1][0] < ambiguity_margin:
        return None
    return scored[0][3]


class BackfillRepository:
    def __init__(self, database):
        self.database = database

    def candidates(self, limit: int) -> list[ExistingTrack]:
        connection = self.database.connect()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """WITH gallery_count AS (
                     SELECT subject_id, count(*) AS count
                     FROM subject_representative_face WHERE active GROUP BY subject_id
                   ), ranked AS (
                     SELECT ft.*, sa.external_source_ref, sa.source_sha256,
                            (sa.metadata->>'generation')::bigint AS source_generation,
                            row_number() OVER (
                              PARTITION BY ft.subject_id, ft.source_id
                              ORDER BY ft.max_quality DESC NULLS LAST, ft.track_id
                            ) AS source_rank
                     FROM face_track ft JOIN source_asset sa USING (source_id)
                     LEFT JOIN gallery_count gc ON gc.subject_id = ft.subject_id
                     WHERE ft.subject_id IS NOT NULL AND COALESCE(gc.count, 0) < 5
                       AND sa.metadata ? 'generation'
                       AND NOT EXISTS (
                         SELECT 1 FROM subject_representative_face representative
                         WHERE representative.subject_id = ft.subject_id
                           AND representative.source_id = ft.source_id
                           AND representative.active
                       )
                   )
                   SELECT subject_id, source_id, track_id, external_source_ref,
                          source_generation, source_sha256, start_ms, end_ms,
                          aggregate_embedding::text, model_version,
                          COALESCE(max_quality, 0)
                   FROM ranked WHERE source_rank = 1
                   ORDER BY subject_id, max_quality DESC NULLS LAST, source_id
                   LIMIT %s""",
                (limit,),
            )
            return [
                ExistingTrack(
                    str(row[0]),
                    str(row[1]),
                    str(row[2]),
                    str(row[3]),
                    int(row[4]),
                    str(row[5]),
                    int(row[6]),
                    int(row[7]),
                    json.loads(row[8]),
                    str(row[9]),
                    float(row[10]),
                )
                for row in cursor.fetchall()
            ]
        finally:
            connection.rollback()
            cursor.close()
            connection.close()

    def stage(
        self,
        existing: ExistingTrack,
        regenerated: RegeneratedTrack,
        representative_id: str,
        object_name: str,
        generation: int,
    ) -> None:
        connection = self.database.connect()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """INSERT INTO subject_representative_face
                     (representative_id, subject_id, source_id, source_track_id,
                      object_name, object_generation, content_type,
                      source_timestamp_ms, quality_score, active)
                   VALUES (%s,%s,%s,%s,%s,%s,'image/jpeg',%s,%s,false)""",
                (
                    representative_id,
                    existing.subject_id,
                    existing.source_id,
                    existing.track_id,
                    object_name,
                    generation,
                    (regenerated.start_ms + regenerated.end_ms) // 2,
                    regenerated.quality,
                ),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def active_ids(self, subject_id: str) -> list[str]:
        connection = self.database.connect()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """SELECT representative_id FROM subject_representative_face
                   WHERE subject_id = %s AND active
                   ORDER BY quality_score DESC NULLS LAST, representative_id LIMIT 5""",
                (subject_id,),
            )
            return [str(row[0]) for row in cursor.fetchall()]
        finally:
            connection.rollback()
            cursor.close()
            connection.close()

    def publish(self, subject_id: str, representative_ids: list[str]) -> None:
        connection = self.database.connect()
        cursor = connection.cursor()
        try:
            cursor.execute(
                "SELECT subject_id FROM subject WHERE subject_id = %s FOR UPDATE",
                (subject_id,),
            )
            if not cursor.fetchone():
                raise ValueError("backfill subject no longer exists")
            cursor.execute(
                """WITH retired AS (
                     UPDATE subject_representative_face
                     SET active = false, retired_at = now()
                     WHERE subject_id = %s AND active
                       AND representative_id <> ALL(%s::uuid[])
                     RETURNING representative_id, object_name, object_generation
                   )
                   INSERT INTO gallery_cleanup_object
                     (representative_id, object_name, object_generation)
                   SELECT representative_id, object_name, object_generation FROM retired
                   ON CONFLICT (object_name, object_generation) DO NOTHING""",
                (subject_id, representative_ids),
            )
            cursor.execute(
                """UPDATE subject_representative_face
                   SET active = true, retired_at = NULL
                   WHERE subject_id = %s AND representative_id = ANY(%s::uuid[])""",
                (subject_id, representative_ids),
            )
            if cursor.rowcount != len(representative_ids):
                raise RuntimeError("not every staged representative belongs to subject")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()


class GalleryBackfill:
    def __init__(self, settings, repository, storage, detector, embedder):
        self.settings = settings
        self.repository = repository
        self.storage = storage
        self.detector = detector
        self.embedder = embedder

    def run(self, limit: int) -> dict[str, Any]:
        from .probe_service import LocalMedia, video_faces

        existing_tracks = self.repository.candidates(limit)
        by_subject: dict[str, list[ExistingTrack]] = {}
        for track in existing_tracks:
            by_subject.setdefault(track.subject_id, []).append(track)
        started = time.monotonic()
        report: dict[str, Any] = {
            "subjects_scanned": len(by_subject),
            "tracks_scanned": len(existing_tracks),
            "associated": 0,
            "skipped": 0,
            "source_failures": 0,
            "subjects_published": 0,
            "source_bytes_scanned": 0,
        }
        source_cache: dict[tuple[str, int], list[RegeneratedTrack] | None] = {}
        assigned: set[tuple[tuple[str, int], int]] = set()
        for subject_id, tracks in by_subject.items():
            publish_ids = self.repository.active_ids(subject_id)
            for existing in tracks:
                if len(publish_ids) >= 5:
                    break
                source_key = (existing.source_uri, existing.source_generation)
                regenerated = source_cache.get(source_key)
                if source_key not in source_cache:
                    try:
                        data = self.storage.download_source_generation(
                            existing.source_uri,
                            existing.source_generation,
                            self.settings.max_video_bytes,
                        )
                        report["source_bytes_scanned"] += len(data)
                        digest = hashlib.sha256(data).hexdigest()
                        if digest != existing.source_sha256:
                            raise ValueError("backfill source checksum mismatch")
                        faces, crops = video_faces(
                            LocalMedia("video/mp4", data, digest),
                            self.settings,
                            self.detector,
                            self.embedder,
                        )
                        regenerated = []
                        for face in faces:
                            prefix = f"track-{face['local_face_id']:06d}/"
                            crop = next(
                                (
                                    item
                                    for item in crops
                                    if item.relative_path.startswith(prefix)
                                ),
                                None,
                            )
                            if crop is not None:
                                regenerated.append(
                                    RegeneratedTrack(
                                        face["local_face_id"],
                                        face["start_ms"],
                                        face["end_ms"],
                                        face["embedding"],
                                        float(face["max_quality"] or 0),
                                        crop.content,
                                    )
                                )
                        source_cache[source_key] = regenerated
                    except (OSError, RuntimeError, ValueError):
                        source_cache[source_key] = None
                        report["source_failures"] += 1
                        regenerated = None
                if regenerated is None:
                    continue
                match = associate_track(existing, regenerated)
                if match is None:
                    report["skipped"] += 1
                    continue
                assignment = (source_key, match.local_id)
                if assignment in assigned:
                    report["skipped"] += 1
                    continue
                assigned.add(assignment)
                representative_id = str(uuid.uuid4())
                object_name, generation = self.storage.upload_gallery_face(
                    subject_id, representative_id, match.crop_jpeg
                )
                self.repository.stage(
                    existing,
                    match,
                    representative_id,
                    object_name,
                    generation,
                )
                publish_ids.append(representative_id)
                report["associated"] += 1
            if publish_ids:
                self.repository.publish(subject_id, publish_ids[:5])
                report["subjects_published"] += 1
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)
        report["association_rate"] = (
            report["associated"] / report["tracks_scanned"]
            if report["tracks_scanned"]
            else 0.0
        )
        return report
