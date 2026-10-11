from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

from .db import pgvector
from .run_repository import queue_selection, request_fingerprint
from .source_merges import SourceMerges, configuration
from .source_registry import register_run_source
from .subject_management import (
    SubjectManagement,
    enrollment_targets,
    recalculate_subjects,
)


@dataclass(frozen=True)
class DetectionWork:
    operation_id: str
    run_id: str
    object_name: str
    generation: int
    content_type: str
    sha256: str
    lease_owner: str


@dataclass(frozen=True)
class MatchGroup:
    group_id: str
    embedding: Any
    representative_object_name: str | None = None
    representative_generation: int | None = None
    quality: float | None = None


@dataclass(frozen=True)
class MatchWork:
    lease_owner: str
    groups: list[MatchGroup]
    handling_policy: str
    object_name: str
    generation: int
    content_type: str
    sha256: str
    object_bytes: int


class InteractiveRepository:
    def __init__(self, database, lease_seconds: int = 3900, *, source_merge_threshold=None):
        self.database = database
        self.lease_seconds = lease_seconds
        self.source_merge_threshold = (configuration({'threshold': source_merge_threshold})
                                       if source_merge_threshold is not None else None)

    def claim_detection(self, run_id: str) -> DetectionWork | None:
        owner = str(uuid.uuid4())
        connection = self.database.connect()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """UPDATE run_operation operation SET state = 'leased', lease_owner = %s,
                          lease_expires_at = now() + (%s * interval '1 second'),
                          attempt_count = attempt_count + 1, updated_at = now()
                   FROM media_run run
                   WHERE operation.run_id = %s AND operation.run_id = run.run_id
                     AND operation.kind = 'detect'
                     AND (operation.state = 'queued' OR
                          (operation.state = 'leased' AND operation.lease_expires_at < now()))
                     AND run.state IN ('queued','detecting') AND NOT run.cancel_requested
                   RETURNING operation.operation_id, run.run_id, run.object_name,
                             run.object_generation, run.content_type, run.source_sha256""",
                (owner, self.lease_seconds, run_id),
            )
            row = cursor.fetchone()
            if not row:
                connection.rollback()
                return None
            cursor.execute(
                """UPDATE media_run SET state = 'detecting', updated_at = now(),
                          row_version = row_version + 1
                   WHERE run_id = %s AND state IN ('queued','detecting')
                     AND NOT cancel_requested""",
                (run_id,),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                return None
            connection.commit()
            return DetectionWork(
                str(row[0]),
                str(row[1]),
                str(row[2]),
                int(row[3]),
                str(row[4]),
                str(row[5]),
                owner,
            )
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def complete_detection(
        self,
        work: DetectionWork,
        groups: list[dict[str, Any]],
        *,
        detector_version: str,
        embedding_version: str,
    ) -> bool:
        connection = self.database.connect()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """SELECT cancel_requested,selection_policy FROM media_run
                   WHERE run_id = %s AND state = 'detecting' FOR UPDATE""",
                (work.run_id,),
            )
            run = cursor.fetchone()
            if not run:
                connection.rollback()
                return False
            if run[0]:
                cursor.execute(
                    """UPDATE media_run SET state = 'cancelled', completed_at = now(),
                              updated_at = now(), row_version = row_version + 1
                       WHERE run_id = %s""",
                    (work.run_id,),
                )
                self._enqueue_cleanup(cursor, work.run_id)
            elif not groups:
                register_run_source(cursor, work.run_id)
                cursor.execute(
                    """UPDATE media_run SET state = 'succeeded', outcome = 'no_faces',
                              completed_at = now(), updated_at = now(), row_version = row_version + 1
                       WHERE run_id = %s""",
                    (work.run_id,),
                )
                self._enqueue_cleanup(cursor, work.run_id)
            else:
                for group in groups:
                    cursor.execute(
                        """INSERT INTO submission_face_group
                             (group_id, run_id, local_group_id, bbox, start_ms, end_ms,
                              quality_summary, preview_object_name, preview_generation,
                              detector_version, embedding_model_version, aggregate_embedding,
                              representative_object_name, representative_generation)
                           VALUES (%s,%s,%s,%s::jsonb,%s,%s,%s::jsonb,%s,%s,%s,%s,%s::vector,%s,%s)""",
                        (
                            group["group_id"],
                            work.run_id,
                            group["local_group_id"],
                            (
                                None
                                if group.get("bbox") is None
                                else json.dumps(group["bbox"])
                            ),
                            group.get("start_ms"),
                            group.get("end_ms"),
                            json.dumps(group["quality"]),
                            group["preview_object_name"],
                            group["preview_generation"],
                            detector_version,
                            embedding_version,
                            pgvector(group["embedding"]),
                            group["representative_object_name"],
                            group["representative_generation"],
                        ),
                    )
                cursor.execute(
                    """UPDATE media_run SET state = 'awaiting_face_selection',
                              updated_at = now(), row_version = row_version + 1
                       WHERE run_id = %s""",
                    (work.run_id,),
                )
                if run[1] == "all_tracks":
                    selected = [str(group["group_id"]) for group in groups]
                    queue_selection(cursor, work.run_id, selected, [])
                    cursor.execute(
                        """UPDATE media_run SET state='matching',selection_fingerprint=%s
                           WHERE run_id=%s""",
                        (
                            request_fingerprint(
                                {"group_ids": sorted(selected), "assignments": []}
                            ),
                            work.run_id,
                        ),
                    )
            cursor.execute(
                """UPDATE run_operation SET state = 'succeeded', lease_owner = NULL,
                          lease_expires_at = NULL, updated_at = now()
                   WHERE operation_id = %s AND lease_owner = %s""",
                (work.operation_id, work.lease_owner),
            )
            connection.commit()
            return True
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    @staticmethod
    def _enqueue_cleanup(cursor, run_id: str) -> None:
        cursor.execute(
            """INSERT INTO run_cleanup_object (run_id, object_name, object_generation)
               SELECT run_id, object_name, object_generation FROM media_run
               WHERE run_id = %s AND object_name IS NOT NULL AND object_generation IS NOT NULL
               UNION ALL
               SELECT run_id, preview_object_name, preview_generation
               FROM submission_face_group WHERE run_id = %s
               UNION ALL
               SELECT run_id, representative_object_name, representative_generation
               FROM submission_face_group WHERE run_id = %s
                 AND representative_object_name IS NOT NULL
                 AND representative_generation IS NOT NULL
               ON CONFLICT (object_name, object_generation) DO NOTHING""",
            (run_id, run_id, run_id),
        )

    def claim_matching(self, run_id: str) -> MatchWork | None:
        owner = str(uuid.uuid4())
        connection = self.database.connect()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """UPDATE run_operation operation SET state = 'leased', lease_owner = %s,
                          lease_expires_at = now() + (%s * interval '1 second'),
                          attempt_count = attempt_count + 1, updated_at = now()
                   FROM media_run run
                   WHERE operation.run_id = %s AND operation.run_id = run.run_id
                     AND operation.kind = 'match'
                     AND (operation.state = 'queued' OR
                          (operation.state = 'leased' AND operation.lease_expires_at < now()
                           AND operation.attempt_count < 3))
                     AND run.state = 'matching' AND NOT run.cancel_requested
                   RETURNING operation.operation_id, run.handling_policy,
                             run.object_name, run.object_generation, run.content_type,
                             run.source_sha256, run.object_bytes""",
                (owner, self.lease_seconds, run_id),
            )
            operation = cursor.fetchone()
            if not operation:
                connection.rollback()
                return None
            cursor.execute(
                """SELECT group_id, aggregate_embedding::text,
                          COALESCE(representative_object_name, preview_object_name),
                          COALESCE(representative_generation, preview_generation),
                          (quality_summary->>'max_quality')::real
                   FROM submission_face_group
                   WHERE run_id = %s AND selected ORDER BY local_group_id""",
                (run_id,),
            )
            groups = [
                MatchGroup(
                    str(row[0]), json.loads(row[1]), str(row[2]), int(row[3]), row[4]
                )
                for row in cursor.fetchall()
            ]
            if not groups:
                connection.rollback()
                return None
            connection.commit()
            return MatchWork(
                owner,
                groups,
                str(operation[1]),
                str(operation[2]),
                int(operation[3]),
                str(operation[4]),
                str(operation[5]),
                int(operation[6]),
            )
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def complete_matching(
        self,
        run_id: str,
        lease_owner: str,
        groups: list[MatchGroup],
        rankings,
        *,
        enrollment_source=None,
        publish_representative=None,
        promote_source=None,
        model_version: str | None = None,
        source_bucket: str | None = None,
    ) -> bool:
        connection = self.database.connect()
        cursor = connection.cursor()
        try:
            for group, ranked in zip(groups, rankings, strict=True):
                for rank, candidate in enumerate(ranked, 1):
                    if (
                        type(candidate.compared_subject_version) is not int
                        or candidate.compared_subject_version < 1
                    ):
                        raise ValueError(
                            "New search results require the compared subject version"
                        )
                    cursor.execute(
                        """INSERT INTO run_candidate
                             (run_id, group_id, subject_id, rank, similarity,
                              display_name_snapshot, source_count, observation_count,
                              page_urls, compared_subject_version)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
                           ON CONFLICT (run_id, group_id, subject_id) DO UPDATE
                           SET rank = EXCLUDED.rank, similarity = EXCLUDED.similarity,
                               display_name_snapshot = EXCLUDED.display_name_snapshot,
                               page_urls = EXCLUDED.page_urls,
                               compared_subject_version = EXCLUDED.compared_subject_version""",
                        (
                            run_id,
                            group.group_id,
                            candidate.subject_id,
                            rank,
                            candidate.similarity,
                            candidate.display_name,
                            len({item["video_uri"] for item in candidate.observations}),
                            len(candidate.observations),
                            json.dumps(list(candidate.page_urls)),
                            candidate.compared_subject_version,
                        ),
                    )
            cursor.execute(
                """UPDATE run_operation SET state = 'succeeded', lease_owner = NULL,
                          lease_expires_at = NULL, updated_at = now()
                   WHERE run_id = %s AND kind = 'match' AND state = 'leased'
                     AND lease_owner = %s""",
                (run_id, lease_owner),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                return False
            if enrollment_source is not None:
                if model_version is None or publish_representative is None:
                    raise ValueError("enrollment configuration is required")
                cursor.execute("SELECT pg_advisory_xact_lock(8675309)")
                source_id, retained_name, retained_generation, retained_bytes = enrollment_source
                cursor.execute(
                    "SELECT source_sha256,source_page_url,content_type FROM media_run WHERE run_id=%s",
                    (run_id,),
                )
                source_sha256, page_url, content_type = cursor.fetchone()
                # The same lock protects the lookup, optional promotion and enrollment.
                # Concurrent duplicate runs therefore cannot publish extra subjects or copies.
                cursor.execute(
                    """SELECT s.source_id,s.storage_kind,s.object_name,s.object_generation,s.object_bytes
                       FROM source_asset s WHERE s.source_sha256=%s AND s.deleted_at IS NULL
                       ORDER BY (s.storage_kind='managed') DESC,
                         EXISTS(SELECT 1 FROM subject_example e WHERE e.source_id=s.source_id) DESC,
                         s.created_at,s.source_id LIMIT 1""",
                    (source_sha256,),
                )
                existing = cursor.fetchone()
                if existing:
                    source_id = str(existing[0])
                cursor.execute(
                    """SELECT EXISTS(SELECT 1 FROM subject_example e JOIN source_asset s USING(source_id)
                       WHERE s.source_sha256=%s)""", (source_sha256,),
                )
                reused = bool(cursor.fetchone()[0])
                if promote_source is not None and (not existing or existing[1] != 'managed'):
                    retained_name, retained_generation, retained_bytes = promote_source(source_id)
                if not existing:
                    cursor.execute(
                        """INSERT INTO source_asset(source_id,external_source_ref,source_sha256,
                           source_page_url,content_type) VALUES (%s,%s,%s,%s,%s)""",
                        (source_id,f"submission:{run_id}",source_sha256,page_url,content_type),
                    )
                if retained_name is not None:
                    if not source_bucket:
                        raise ValueError("Retained source bucket is required")
                    cursor.execute(
                        """UPDATE source_asset SET object_bucket=%s,object_name=%s,
                           object_generation=%s,object_bytes=%s,encryption_mode='CSEK',storage_kind='managed'
                           WHERE source_id=%s""",
                        (source_bucket,retained_name,retained_generation,retained_bytes,source_id),
                    )
                if page_url:
                    cursor.execute(
                        """INSERT INTO source_url(source_sha256,page_url) VALUES (%s,%s)
                           ON CONFLICT DO NOTHING""", (source_sha256,page_url),
                    )
                effective_source_id = source_id
                # Do not even allocate assignment subjects for a duplicate video.
                targets = {} if reused else enrollment_targets(cursor, run_id, model_version)
                affected_subjects = set()
                for group, ranked in zip([] if reused else groups, [] if reused else rankings, strict=True):
                    if group.group_id not in targets:
                        raise ValueError("Selected track has no enrollment assignment")
                    subject_id = targets[group.group_id]
                    decision = "created"
                    cursor.execute(
                        """INSERT INTO subject(subject_id,model_version) VALUES (%s,%s)
                           ON CONFLICT (subject_id) DO NOTHING""",
                        (subject_id, model_version),
                    )
                    affected_subjects.add(subject_id)
                    cursor.execute(
                        """UPDATE submission_face_group
                           SET enrollment_decision=%s,enrolled_at=now()
                           WHERE group_id=%s AND run_id=%s AND enrolled_at IS NULL
                           RETURNING group_id""",
                        (decision, group.group_id, run_id),
                    )
                    if not cursor.fetchone():
                        continue
                    cursor.execute(
                        """INSERT INTO subject_example
                             (example_id,subject_id,source_id,submission_group_id,embedding,
                              model_version,start_ms,end_ms,quality_score)
                           SELECT group_id,%s,%s,group_id,aggregate_embedding,%s,start_ms,end_ms,
                                  (quality_summary->>'max_quality')::real
                           FROM submission_face_group WHERE group_id=%s
                           ON CONFLICT (submission_group_id) DO NOTHING""",
                        (
                            subject_id,
                            effective_source_id,
                            model_version,
                            group.group_id,
                        ),
                    )
                    representative_id = str(
                        uuid.uuid5(uuid.UUID(group.group_id), "gallery-representative")
                    )
                    object_name, generation = publish_representative(
                        subject_id, representative_id, group
                    )
                    # Keep failed/rolled-back publications eligible for cleanup. This
                    # separate commit survives rollback of the enrollment transaction.
                    self._queue_gallery_upload(
                        representative_id, object_name, generation
                    )
                    cursor.execute(
                        """INSERT INTO subject_representative_face
                             (representative_id, object_name,
                              object_generation, content_type, quality_score, active, example_id)
                           VALUES (%s,%s,%s,'image/jpeg',%s,true,%s)
                           ON CONFLICT (representative_id) DO NOTHING""",
                        (
                            representative_id,
                            object_name,
                            generation,
                            group.quality,
                            group.group_id,
                        ),
                    )
                    cursor.execute(
                        """DELETE FROM gallery_cleanup_object
                           WHERE object_name=%s AND object_generation=%s AND state='queued'""",
                        (object_name, generation),
                    )
                    if cursor.rowcount != 1:
                        raise RuntimeError(
                            "representative cleanup is already in progress"
                        )
                recalculate_subjects(cursor, affected_subjects)
                if not reused and self.source_merge_threshold is not None:
                    report = SourceMerges(SubjectManagement(self.database)).auto_merge(
                        cursor, effective_source_id, run_id, self.source_merge_threshold)
                    cursor.execute("""UPDATE source_asset SET metadata=jsonb_set(
                        COALESCE(metadata,'{}'::jsonb),'{automatic_matching}',%s::jsonb)
                        WHERE source_id=%s""", (json.dumps(report), effective_source_id))
                cursor.execute(
                    """UPDATE media_run SET retained_source_id=%s,
                              enrollment_completed_at=now(),enrollment_reused=%s
                       WHERE run_id=%s""",
                    (effective_source_id, reused, run_id),
                )
            cursor.execute(
                """UPDATE media_run SET state = 'succeeded', outcome = 'candidates',
                          completed_at = now(), updated_at = now(), row_version = row_version + 1
                   WHERE run_id = %s AND state = 'matching'""",
                (run_id,),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                return False
            register_run_source(cursor, run_id)
            self._enqueue_cleanup(cursor, run_id)
            connection.commit()
            return True
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def _queue_gallery_upload(self, representative_id, object_name, generation):
        connection = self.database.connect()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """INSERT INTO gallery_cleanup_object
                     (representative_id, object_name, object_generation)
                   VALUES (%s,%s,%s)
                   ON CONFLICT (object_name, object_generation) DO NOTHING""",
                (representative_id, object_name, generation),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def rank(self, groups: list[MatchGroup], model_version: str, top_k: int):
        return self.database.search_subjects(
            [group.embedding for group in groups], model_version, top_k
        )

    def retained_source(self, sha256: str):
        connection = self.database.connect()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """SELECT source_id, object_name, object_generation, object_bytes
                   FROM source_asset WHERE source_sha256=%s AND storage_kind='managed'
                     AND deleted_at IS NULL""",
                (sha256,),
            )
            row = cursor.fetchone()
            return (
                None
                if row is None
                else (str(row[0]), str(row[1]), int(row[2]), int(row[3]))
            )
        finally:
            connection.rollback()
            cursor.close()
            connection.close()

    def fail(self, run_id: str, step: str, error_code: str, retryable: bool) -> None:
        if step not in {"detecting", "matching"}:
            raise ValueError("invalid interactive failure step")
        kind = "detect" if step == "detecting" else "match"
        connection = self.database.connect()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """UPDATE media_run SET state = 'failed', failed_step = %s,
                          error_code = %s, retryable = %s, completed_at = now(),
                          updated_at = now(), row_version = row_version + 1
                   WHERE run_id = %s AND state = %s""",
                (step, error_code, retryable, run_id, step),
            )
            cursor.execute(
                """UPDATE run_operation SET state = 'failed', last_error_code = %s,
                          lease_owner = NULL, lease_expires_at = NULL, updated_at = now()
                   WHERE run_id = %s AND kind = %s AND state = 'leased'""",
                (error_code, run_id, kind),
            )
            if not retryable:
                self._enqueue_cleanup(cursor, run_id)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()
