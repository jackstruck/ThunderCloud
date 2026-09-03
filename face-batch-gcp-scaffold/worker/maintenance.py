from __future__ import annotations

import uuid
from dataclasses import dataclass

from google.api_core.exceptions import GoogleAPIError, NotFound


@dataclass(frozen=True)
class CleanupObject:
    cleanup_id: str
    object_name: str
    generation: int
    lease_owner: str
    attempt_count: int


class MaintenanceRepository:
    def __init__(self, database, lease_seconds: int = 300):
        self.database = database
        self.lease_seconds = lease_seconds

    def expire_runs(self) -> int:
        connection = self.database.connect()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """UPDATE media_run SET state = 'expired', cancel_requested = true,
                          source_page_url = NULL,
                          updated_at = now(), completed_at = COALESCE(completed_at, now()),
                          row_version = row_version + 1
                   WHERE expires_at <= now() AND state <> 'expired'
                   RETURNING run_id"""
            )
            expired_ids = [str(row[0]) for row in cursor.fetchall()]
            if not expired_ids:
                connection.commit()
                return 0
            cursor.execute(
                """INSERT INTO run_cleanup_object
                     (run_id, object_name, object_generation)
                   SELECT run_id, object_name, object_generation FROM media_run
                   WHERE run_id = ANY(%s::uuid[]) AND object_name IS NOT NULL
                     AND object_generation IS NOT NULL
                   UNION ALL
                   SELECT run_id, preview_object_name, preview_generation
                   FROM submission_face_group WHERE run_id = ANY(%s::uuid[])
                   ON CONFLICT (object_name, object_generation) DO NOTHING""",
                (expired_ids, expired_ids),
            )
            cursor.execute(
                "DELETE FROM submission_face_group WHERE run_id = ANY(%s::uuid[])",
                (expired_ids,),
            )
            connection.commit()
            return len(expired_ids)
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def claim_cleanup(self) -> CleanupObject | None:
        connection = self.database.connect()
        cursor = connection.cursor()
        owner = str(uuid.uuid4())
        try:
            cursor.execute(
                """WITH candidate AS (
                     SELECT cleanup_id FROM run_cleanup_object
                     WHERE state = 'queued' OR
                           (state = 'leased' AND lease_expires_at < now())
                     ORDER BY created_at, cleanup_id
                     FOR UPDATE SKIP LOCKED LIMIT 1
                   )
                   UPDATE run_cleanup_object cleanup
                   SET state = 'leased', lease_owner = %s,
                       lease_expires_at = now() + (%s * interval '1 second'),
                       attempt_count = attempt_count + 1, updated_at = now()
                   FROM candidate WHERE cleanup.cleanup_id = candidate.cleanup_id
                   RETURNING cleanup.cleanup_id, cleanup.object_name,
                             cleanup.object_generation, cleanup.attempt_count""",
                (owner, self.lease_seconds),
            )
            row = cursor.fetchone()
            connection.commit()
            if not row:
                return None
            return CleanupObject(
                str(row[0]), str(row[1]), int(row[2]), owner, int(row[3])
            )
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def unresolved_uploads(self) -> list[tuple[str, str]]:
        connection = self.database.connect()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """SELECT run_id, object_name FROM media_run
                   WHERE state IN ('cancelled','expired') AND source_kind = 'upload'
                     AND object_name IS NOT NULL AND object_generation IS NULL"""
            )
            return [(str(row[0]), str(row[1])) for row in cursor.fetchall()]
        finally:
            connection.rollback()
            cursor.close()
            connection.close()

    def record_orphan(self, run_id: str, object_name: str, generation: int) -> None:
        connection = self.database.connect()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """UPDATE media_run SET object_generation = %s, updated_at = now()
                   WHERE run_id = %s AND object_name = %s AND object_generation IS NULL""",
                (generation, run_id, object_name),
            )
            cursor.execute(
                """INSERT INTO run_cleanup_object
                     (run_id, object_name, object_generation)
                   VALUES (%s,%s,%s)
                   ON CONFLICT (object_name, object_generation) DO NOTHING""",
                (run_id, object_name, generation),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def finish_cleanup(
        self, item: CleanupObject, error_code: str | None = None
    ) -> None:
        connection = self.database.connect()
        cursor = connection.cursor()
        try:
            retry = error_code is not None and item.attempt_count < 5
            state = "queued" if retry else ("failed" if error_code else "succeeded")
            cursor.execute(
                """UPDATE run_cleanup_object SET state = %s, last_error_code = %s,
                          lease_owner = NULL, lease_expires_at = NULL, updated_at = now()
                   WHERE cleanup_id = %s AND state = 'leased' AND lease_owner = %s""",
                (state, error_code, item.cleanup_id, item.lease_owner),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def claim_gallery_cleanup(self) -> CleanupObject | None:
        connection = self.database.connect()
        cursor = connection.cursor()
        owner = str(uuid.uuid4())
        try:
            cursor.execute(
                """WITH candidate AS (
                     SELECT cleanup_id FROM gallery_cleanup_object
                     WHERE delete_after <= now() AND
                       (state = 'queued' OR (state = 'leased' AND lease_expires_at < now()))
                     ORDER BY delete_after, cleanup_id
                     FOR UPDATE SKIP LOCKED LIMIT 1
                   )
                   UPDATE gallery_cleanup_object cleanup
                   SET state = 'leased', lease_owner = %s,
                       lease_expires_at = now() + (%s * interval '1 second'),
                       attempt_count = attempt_count + 1, updated_at = now()
                   FROM candidate WHERE cleanup.cleanup_id = candidate.cleanup_id
                   RETURNING cleanup.cleanup_id, cleanup.object_name,
                             cleanup.object_generation, cleanup.attempt_count""",
                (owner, self.lease_seconds),
            )
            row = cursor.fetchone()
            connection.commit()
            if not row:
                return None
            return CleanupObject(
                str(row[0]), str(row[1]), int(row[2]), owner, int(row[3])
            )
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def finish_gallery_cleanup(
        self, item: CleanupObject, error_code: str | None = None
    ) -> None:
        connection = self.database.connect()
        cursor = connection.cursor()
        try:
            retry = error_code is not None and item.attempt_count < 5
            state = "queued" if retry else ("failed" if error_code else "succeeded")
            cursor.execute(
                """UPDATE gallery_cleanup_object SET state = %s, last_error_code = %s,
                          lease_owner = NULL, lease_expires_at = NULL, updated_at = now()
                   WHERE cleanup_id = %s AND state = 'leased' AND lease_owner = %s
                   RETURNING representative_id""",
                (state, error_code, item.cleanup_id, item.lease_owner),
            )
            row = cursor.fetchone()
            if row and state == "succeeded":
                cursor.execute(
                    """DELETE FROM subject_representative_face
                       WHERE representative_id = %s AND NOT active""",
                    (row[0],),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()


def maintain(repository, storage) -> dict[str, int]:
    expired = repository.expire_runs()
    for run_id, object_name in repository.unresolved_uploads():
        try:
            generation = storage.temporary_generation(object_name)
        except NotFound:
            continue
        repository.record_orphan(run_id, object_name, generation)
    deleted = failed = 0
    while item := repository.claim_cleanup():
        try:
            storage.delete_temporary(item.object_name, item.generation)
        except NotFound:
            # The idempotent desired state is already true.
            repository.finish_cleanup(item)
            deleted += 1
        except (GoogleAPIError, OSError, RuntimeError, ValueError):
            repository.finish_cleanup(item, "deletion_failed")
            failed += 1
        else:
            repository.finish_cleanup(item)
            deleted += 1
    gallery_deleted = 0
    while item := repository.claim_gallery_cleanup():
        try:
            storage.delete_gallery_face(item.object_name, item.generation)
        except NotFound:
            repository.finish_gallery_cleanup(item)
            gallery_deleted += 1
        except (GoogleAPIError, OSError, RuntimeError, ValueError):
            repository.finish_gallery_cleanup(item, "gallery_deletion_failed")
            failed += 1
        else:
            repository.finish_gallery_cleanup(item)
            gallery_deleted += 1
    return {
        "expired": expired,
        "deleted": deleted,
        "gallery_deleted": gallery_deleted,
        "failed": failed,
    }
