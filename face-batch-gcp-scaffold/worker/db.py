from __future__ import annotations

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
                 AND s.merged_into_subject_id IS NULL
               ORDER BY s.canonical_embedding <=> %s::vector, s.subject_id
               LIMIT %s""",
            (vector, embedding_model_version, vector, top_k),
        )
        ranked = []
        for subject_id, similarity, display_name, subject_version in cursor.fetchall():
            cursor.execute(
                """SELECT sa.external_source_ref,sa.source_sha256,e.example_id,
                          e.start_ms,e.end_ms,e.quality_score,e.quality_score,
                          COALESCE(pj.completed_at,se.enrolled_at),sa.source_id,pj.job_id
                   FROM source_asset sa JOIN subject_example e USING(source_id)
                   LEFT JOIN face_track ft ON ft.track_id=e.face_track_id
                   LEFT JOIN processing_job pj ON pj.job_id=ft.processing_job_id
                   LEFT JOIN submission_face_group se ON se.group_id=e.submission_group_id
                   WHERE e.subject_id=%s
                   ORDER BY e.created_at,e.example_id""",
                (subject_id,),
            )
            observations = tuple(
                {
                    "video_uri": row[0],
                    "source_sha256": str(row[1]),
                    "track_id": str(row[2]),
                    "start_ms": None if row[3] is None else int(row[3]),
                    "end_ms": None if row[4] is None else int(row[4]),
                    "max_quality": None if row[5] is None else float(row[5]),
                    "mean_quality": None if row[6] is None else float(row[6]),
                    "processing_completed_at": row[7],
                    "source_id": str(row[8]),
                    "processing_job_id": None if row[9] is None else str(row[9]),
                }
                for row in cursor.fetchall()
            )
            cursor.execute(
                """SELECT DISTINCT source_page_url
                   FROM source_asset WHERE source_id IN (
                     SELECT source_id FROM subject_example WHERE subject_id=%s
                   ) AND source_page_url IS NOT NULL
                   ORDER BY 1""",
                (subject_id,),
            )
            page_urls = tuple(str(row[0]) for row in cursor.fetchall())
            ranked.append(
                RankedSubject(
                    str(subject_id),
                    float(similarity),
                    display_name,
                    observations,
                    page_urls,
                    int(subject_version),
                )
            )
        return ranked

    def search_subjects(
        self, embeddings, embedding_model_version: str, top_k: int
    ) -> list[list[RankedSubject]]:
        """Search the gallery inside an explicitly read-only transaction."""
        connection = self.connect()
        cursor = connection.cursor()
        try:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            return [
                self.rank_subjects(cursor, embedding, embedding_model_version, top_k)
                for embedding in embeddings
            ]
        finally:
            connection.rollback()
            cursor.close()
            connection.close()
