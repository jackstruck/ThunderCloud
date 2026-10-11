from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from typing import Any


def pgvector(vector) -> str:
    return "[" + ",".join(format(float(value), ".9g") for value in vector) + "]"


@dataclass(frozen=True)
class RankedSubject:
    subject_id: str
    similarity: float
    display_name: str | None = None
    observations: tuple[dict[str, Any], ...] = ()
    page_urls: tuple[str, ...] = ()
    compared_subject_version: int | None = None


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
        self._pool = None
        self._pid = os.getpid()
        self._lock = threading.Lock()

    def _open(self):
        from google.cloud.sql.connector import Connector, IPTypes

        with self._lock:
            if self._connector is None:
                self._connector = Connector(refresh_strategy="LAZY")
        return self._connector.connect(
            self.instance,
            "pg8000",
            user=self.user,
            db=self.database,
            enable_iam_auth=True,
            ip_type=(IPTypes.PRIVATE if self.ip_type == "PRIVATE" else IPTypes.PUBLIC),
        )

    def connect(self):
        # Gunicorn creates the application after fork. Also discard inherited pool
        # references if a caller explicitly preloads an application before forking.
        if self._pid != os.getpid():
            self._pool = self._connector = None
            self._lock = threading.Lock()
            self._pid = os.getpid()
        with self._lock:
            if self._pool is None:
                import pg8000
                from sqlalchemy import event
                from sqlalchemy.dialects.postgresql.pg8000 import PGDialect_pg8000
                from sqlalchemy.pool import QueuePool

                size = int(os.getenv("FACE_DB_POOL_SIZE", "1"))
                timeout = float(os.getenv("FACE_DB_POOL_TIMEOUT", "5"))
                if size < 1 or timeout <= 0:
                    raise ValueError("Database pool size and timeout must be positive")
                self._pool = QueuePool(
                    self._open, pool_size=size, max_overflow=0, timeout=timeout,
                    recycle=2700, pre_ping=True, use_lifo=True,
                    dialect=PGDialect_pg8000(dbapi=pg8000), reset_on_return=None,
                )
                event.listen(self._pool, "reset", self._reset)
            pool = self._pool
        connection = pool.connect()
        # The pg8000 pre-ping starts a transaction. Clear it so callers can
        # immediately select their isolation level with SET TRANSACTION.
        connection.rollback()
        return connection

    @staticmethod
    def _reset(connection, _record, state):
        if state.terminate_only:
            return
        # Roll back unfinished work, then clear session settings, advisory locks,
        # temporary tables and prepared statements before the next borrower.
        connection.rollback()
        connection.autocommit = True
        try:
            cursor = connection.cursor()
            try:
                cursor.execute("DISCARD ALL")
            finally:
                cursor.close()
        finally:
            connection.autocommit = False

    def close(self) -> None:
        if self._pool is not None:
            self._pool.dispose()
            self._pool = None
        if self._connector is not None:
            self._connector.close()
            self._connector = None

    @staticmethod
    def rank_subjects(
        cursor, embedding, embedding_model_version: str, top_k: int
    ) -> list[RankedSubject]:
        if not 1 <= top_k <= 100:
            raise ValueError("top_k must be between 1 and 100")
        vector = pgvector(embedding)
        cursor.execute(
            """SELECT s.subject_id, 1 - (s.canonical_embedding <=> %s::vector) AS similarity,
                      i.display_name, s.row_version
               FROM subject s
               LEFT JOIN identity i ON i.identity_id = s.identity_id
               WHERE s.canonical_embedding IS NOT NULL AND s.model_version = %s
                 AND s.merged_into_subject_id IS NULL AND NOT s.matching_excluded
               ORDER BY s.canonical_embedding <=> %s::vector, s.subject_id
               LIMIT %s""",
            (vector, embedding_model_version, vector, top_k),
        )
        rows = cursor.fetchall()
        if not rows:
            return []
        ids = [str(row[0]) for row in rows]
        cursor.execute(
            """SELECT e.subject_id,sa.external_source_ref,sa.source_sha256,e.example_id,
                      e.start_ms,e.end_ms,e.quality_score,e.quality_score,
                      COALESCE(pj.completed_at,se.enrolled_at),sa.source_id,pj.job_id
               FROM source_asset sa JOIN subject_example e USING(source_id)
               LEFT JOIN face_track ft ON ft.track_id=e.face_track_id
               LEFT JOIN processing_job pj ON pj.job_id=ft.processing_job_id
               LEFT JOIN submission_face_group se ON se.group_id=e.submission_group_id
               WHERE e.subject_id=ANY(%s::uuid[]) AND e.disabled_at IS NULL
               ORDER BY e.subject_id,e.created_at,e.example_id""", (ids,))
        observations = {sid: [] for sid in ids}
        for sid, *row in cursor.fetchall():
            observations[str(sid)].append({
                "video_uri": row[0], "source_sha256": str(row[1]), "track_id": str(row[2]),
                "start_ms": None if row[3] is None else int(row[3]),
                "end_ms": None if row[4] is None else int(row[4]),
                "max_quality": None if row[5] is None else float(row[5]),
                "mean_quality": None if row[6] is None else float(row[6]),
                "processing_completed_at": row[7], "source_id": str(row[8]),
                "processing_job_id": None if row[9] is None else str(row[9]),
            })
        cursor.execute(
            """SELECT DISTINCT e.subject_id,p.page_url FROM subject_example e
               JOIN source_page_link p USING(source_id)
               WHERE e.subject_id=ANY(%s::uuid[]) AND p.page_url IS NOT NULL
               ORDER BY e.subject_id,p.page_url""", (ids,))
        urls = {sid: [] for sid in ids}
        for sid, url in cursor.fetchall():
            urls[str(sid)].append(str(url))
        ranked = [RankedSubject(str(sid), float(similarity), name,
                    tuple(observations[str(sid)]), tuple(urls[str(sid)]), int(version))
                  for sid, similarity, name, version in rows]
        return ranked

    def search_subjects(
        self, embeddings, embedding_model_version: str, top_k: int
    ) -> list[list[RankedSubject]]:
        """Search the gallery inside an explicitly read-only transaction."""
        connection = self.connect()
        cursor = connection.cursor()
        try:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            cursor.execute("SET LOCAL hnsw.iterative_scan = 'strict_order'")
            return [
                self.rank_subjects(cursor, embedding, embedding_model_version, top_k)
                for embedding in embeddings
            ]
        finally:
            connection.rollback()
            cursor.close()
            connection.close()
