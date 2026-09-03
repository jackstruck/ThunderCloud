from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

from .db import pgvector


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


class InteractiveRepository:
    def __init__(self, database, lease_seconds: int = 1200):
        self.database = database
        self.lease_seconds = lease_seconds

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
                """SELECT cancel_requested FROM media_run
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
                              detector_version, embedding_model_version, aggregate_embedding)
                           VALUES (%s,%s,%s,%s::jsonb,%s,%s,%s::jsonb,%s,%s,%s,%s,%s::vector)""",
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
                        ),
                    )
                cursor.execute(
                    """UPDATE media_run SET state = 'awaiting_face_selection',
                              updated_at = now(), row_version = row_version + 1
                       WHERE run_id = %s""",
                    (work.run_id,),
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
               ON CONFLICT (object_name, object_generation) DO NOTHING""",
            (run_id, run_id),
        )

    def claim_matching(self, run_id: str) -> tuple[str, list[MatchGroup]] | None:
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
                     AND operation.kind = 'match' AND operation.state = 'queued'
                     AND run.state = 'matching'
                   RETURNING operation.operation_id""",
                (owner, self.lease_seconds, run_id),
            )
            operation = cursor.fetchone()
            if not operation:
                connection.rollback()
                return None
            cursor.execute(
                """SELECT group_id, aggregate_embedding::text
                   FROM submission_face_group
                   WHERE run_id = %s AND selected ORDER BY local_group_id""",
                (run_id,),
            )
            groups = [
                MatchGroup(str(row[0]), json.loads(row[1])) for row in cursor.fetchall()
            ]
            if not groups:
                connection.rollback()
                return None
            connection.commit()
            return owner, groups
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
    ) -> bool:
        connection = self.database.connect()
        cursor = connection.cursor()
        try:
            for group, ranked in zip(groups, rankings, strict=True):
                for rank, candidate in enumerate(ranked, 1):
                    cursor.execute(
                        """INSERT INTO run_candidate
                             (run_id, group_id, subject_id, rank, similarity,
                              display_name_snapshot, source_count, observation_count)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                           ON CONFLICT (run_id, group_id, subject_id) DO UPDATE
                           SET rank = EXCLUDED.rank, similarity = EXCLUDED.similarity,
                               display_name_snapshot = EXCLUDED.display_name_snapshot""",
                        (
                            run_id,
                            group.group_id,
                            candidate.subject_id,
                            rank,
                            candidate.similarity,
                            candidate.display_name,
                            len({item["video_uri"] for item in candidate.observations}),
                            len(candidate.observations),
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
            cursor.execute(
                """UPDATE media_run SET state = 'succeeded', outcome = 'candidates',
                          completed_at = now(), updated_at = now(), row_version = row_version + 1
                   WHERE run_id = %s AND state = 'matching'""",
                (run_id,),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                return False
            self._enqueue_cleanup(cursor, run_id)
            connection.commit()
            return True
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
