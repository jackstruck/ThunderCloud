from __future__ import annotations

import json
from dataclasses import dataclass

from .models import Match, TrackTemplate


def pgvector(vector) -> str:
    return "[" + ",".join(format(float(value), ".9g") for value in vector) + "]"


@dataclass(frozen=True)
class Versions:
    worker: str
    detector: str
    embedding: str
    threshold: str


class Database:
    def __init__(
        self, instance: str, user: str, database: str, ip_type: str = "PUBLIC"
    ):
        if ip_type not in {"PUBLIC", "PRIVATE"}:
            raise ValueError("Cloud SQL IP type must be PUBLIC or PRIVATE")
        self.instance = instance
        self.user = user
        self.database = database
        self.ip_type = ip_type
        self._connector = None

    def connect(self):
        from google.cloud.sql.connector import Connector, IPTypes

        if self._connector is None:
            self._connector = Connector()
        return self._connector.connect(
            self.instance,
            "pg8000",
            user=self.user,
            db=self.database,
            enable_iam_auth=True,
            ip_type=(IPTypes.PRIVATE if self.ip_type == "PRIVATE" else IPTypes.PUBLIC),
        )

    def close(self) -> None:
        if self._connector is not None:
            self._connector.close()
            self._connector = None

    def commit_results(
        self,
        *,
        job_id: str,
        idempotency_key: str,
        external_source_ref: str,
        source_sha256: str,
        source_metadata: dict,
        versions: Versions,
        templates: list[TrackTemplate],
        top_k: int,
        threshold: float,
        matching_enabled: bool,
    ) -> bool:
        connection = self.connect()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """INSERT INTO source_asset (external_source_ref, source_sha256, metadata)
                   VALUES (%s, %s, %s::jsonb)
                   ON CONFLICT (external_source_ref, source_sha256)
                   DO UPDATE SET external_source_ref = EXCLUDED.external_source_ref
                   RETURNING source_id""",
                (external_source_ref, source_sha256, json.dumps(source_metadata)),
            )
            source_id = cursor.fetchone()[0]
            cursor.execute(
                "SELECT status FROM processing_job WHERE idempotency_key = %s FOR UPDATE",
                (idempotency_key,),
            )
            existing = cursor.fetchone()
            if existing and existing[0] == "succeeded":
                connection.commit()
                return False
            if existing:
                cursor.execute(
                    "DELETE FROM face_track WHERE processing_job_id = (SELECT job_id FROM processing_job WHERE idempotency_key = %s)",
                    (idempotency_key,),
                )
                cursor.execute(
                    """UPDATE processing_job SET status='running', error_code=NULL, started_at=now(),
                       completed_at=NULL WHERE idempotency_key=%s RETURNING job_id""",
                    (idempotency_key,),
                )
                effective_job_id = cursor.fetchone()[0]
            else:
                cursor.execute(
                    """INSERT INTO processing_job
                       (job_id, idempotency_key, source_id, worker_version, detector_version,
                        embedding_model_version, threshold_version, status, started_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, 'running', now()) RETURNING job_id""",
                    (
                        job_id,
                        idempotency_key,
                        source_id,
                        versions.worker,
                        versions.detector,
                        versions.embedding,
                        versions.threshold,
                    ),
                )
                effective_job_id = cursor.fetchone()[0]

            for template in templates:
                match = (
                    self._match(cursor, template.embedding, top_k, threshold)
                    if matching_enabled
                    else Match(None, None, "unknown")
                )
                subject_id = match.subject_id
                if subject_id is None:
                    cursor.execute(
                        """INSERT INTO subject (canonical_embedding, model_version, sample_count)
                           VALUES (%s::vector, %s, 1) RETURNING subject_id""",
                        (pgvector(template.embedding), versions.embedding),
                    )
                    subject_id = cursor.fetchone()[0]
                cursor.execute(
                    """INSERT INTO face_track
                       (source_id, processing_job_id, subject_id, local_track_id, start_ms, end_ms,
                        aggregate_embedding, model_version, observation_count, embedded_count,
                        max_quality, mean_quality, best_candidate_subject_id, best_candidate_score, decision)
                       VALUES (%s,%s,%s,%s,%s,%s,%s::vector,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        source_id,
                        effective_job_id,
                        subject_id,
                        template.local_track_id,
                        template.start_ms,
                        template.end_ms,
                        pgvector(template.embedding),
                        versions.embedding,
                        template.observation_count,
                        template.embedded_count,
                        template.max_quality,
                        template.mean_quality,
                        match.subject_id,
                        match.score,
                        match.decision,
                    ),
                )
            cursor.execute(
                "UPDATE processing_job SET status='succeeded', completed_at=now() WHERE job_id=%s",
                (effective_job_id,),
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
    def _match(cursor, embedding, top_k: int, threshold: float) -> Match:
        cursor.execute(
            """SELECT subject_id, 1 - (canonical_embedding <=> %s::vector) AS similarity
               FROM subject WHERE canonical_embedding IS NOT NULL
               ORDER BY canonical_embedding <=> %s::vector LIMIT %s""",
            (pgvector(embedding), pgvector(embedding), top_k),
        )
        row = cursor.fetchone()
        if row is None:
            return Match(None, None, "unknown")
        score = float(row[1])
        if score >= threshold:
            return Match(str(row[0]), score, "matched")
        return Match(None, score, "unknown")
