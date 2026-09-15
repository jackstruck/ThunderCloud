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
        from .subject_management import SubjectManagement

        return SubjectManagement(self.database).subject(subject_id)


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
