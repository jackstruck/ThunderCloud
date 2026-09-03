from __future__ import annotations

from dataclasses import dataclass

from .run_repository import RunNotFoundError


@dataclass(frozen=True)
class PrivateImage:
    object_name: str
    generation: int


class GalleryRepository:
    def __init__(self, database):
        self.database = database

    def preview(self, run_id: str, group_id: str) -> PrivateImage:
        return self._one(
            """SELECT face.preview_object_name, face.preview_generation
               FROM submission_face_group face JOIN media_run run USING (run_id)
               WHERE face.run_id = %s AND face.group_id = %s AND run.state <> 'expired'""",
            (run_id, group_id),
        )

    def representative(self, representative_id: str) -> PrivateImage:
        return self._one(
            """SELECT object_name, object_generation
               FROM subject_representative_face
               WHERE representative_id = %s AND active""",
            (representative_id,),
        )

    def _one(self, sql: str, parameters) -> PrivateImage:
        connection = self.database.connect()
        cursor = connection.cursor()
        try:
            cursor.execute(sql, parameters)
            row = cursor.fetchone()
            if not row:
                raise RunNotFoundError("image")
            return PrivateImage(str(row[0]), int(row[1]))
        finally:
            connection.rollback()
            cursor.close()
            connection.close()

    def subject(self, subject_id: str) -> dict:
        connection = self.database.connect()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """SELECT subject.subject_id, identity.display_name,
                          subject.model_version, subject.sample_count,
                          count(DISTINCT face_track.source_id), count(face_track.track_id)
                   FROM subject LEFT JOIN identity USING (identity_id)
                   LEFT JOIN face_track USING (subject_id)
                   WHERE subject.subject_id = %s
                   GROUP BY subject.subject_id, identity.display_name""",
                (subject_id,),
            )
            row = cursor.fetchone()
            if not row:
                raise RunNotFoundError("subject")
            cursor.execute(
                """SELECT representative_id, quality_score, source_timestamp_ms
                   FROM subject_representative_face
                   WHERE subject_id = %s AND active
                   ORDER BY quality_score DESC NULLS LAST, representative_id LIMIT 20""",
                (subject_id,),
            )
            return {
                "subject_id": str(row[0]),
                "display_name": row[1],
                "model_version": row[2],
                "sample_count": int(row[3]),
                "source_count": int(row[4]),
                "observation_count": int(row[5]),
                "representative_faces": [
                    {
                        "representative_id": str(face[0]),
                        "url": f"/api/gallery/faces/{face[0]}",
                        "quality_score": None if face[1] is None else float(face[1]),
                        "source_timestamp_ms": face[2],
                    }
                    for face in cursor.fetchall()
                ],
            }
        finally:
            connection.rollback()
            cursor.close()
            connection.close()


class GalleryService:
    def __init__(self, repository, storage):
        self.repository = repository
        self.storage = storage

    def preview(self, run_id: str, group_id: str) -> bytes:
        image = self.repository.preview(run_id, group_id)
        return self.storage.download_private_jpeg(image.object_name, image.generation)

    def representative(self, representative_id: str) -> bytes:
        image = self.repository.representative(representative_id)
        return self.storage.download_private_jpeg(image.object_name, image.generation)

    def subject(self, subject_id: str) -> dict:
        return self.repository.subject(subject_id)
