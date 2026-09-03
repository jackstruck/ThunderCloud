from __future__ import annotations

import uuid
from dataclasses import dataclass


@dataclass(frozen=True)
class FetchWork:
    operation_id: str
    run_id: str
    source_url: str
    attempt_count: int
    lease_owner: str


class IngestRepository:
    def __init__(self, database, lease_seconds: int = 300):
        self.database = database
        self.lease_seconds = lease_seconds

    def claim_fetch(self) -> FetchWork | None:
        connection = self.database.connect()
        cursor = connection.cursor()
        owner = str(uuid.uuid4())
        try:
            cursor.execute(
                """WITH candidate AS (
                     SELECT operation_id FROM run_operation
                     WHERE kind = 'fetch'
                       AND (state = 'queued' OR (state = 'leased' AND lease_expires_at < now()))
                     ORDER BY created_at, operation_id
                     FOR UPDATE SKIP LOCKED LIMIT 1
                   )
                   UPDATE run_operation operation
                   SET state = 'leased', lease_owner = %s,
                       lease_expires_at = now() + (%s * interval '1 second'),
                       attempt_count = attempt_count + 1, updated_at = now()
                   FROM candidate, media_run run
                   WHERE operation.operation_id = candidate.operation_id
                     AND run.run_id = operation.run_id
                     AND run.state = 'fetching' AND NOT run.cancel_requested
                   RETURNING operation.operation_id, operation.run_id,
                             run.source_page_url, operation.attempt_count""",
                (owner, self.lease_seconds),
            )
            row = cursor.fetchone()
            connection.commit()
            if not row:
                return None
            return FetchWork(str(row[0]), str(row[1]), str(row[2]), int(row[3]), owner)
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def complete_fetch(
        self,
        work: FetchWork,
        *,
        final_url: str,
        source_adapter: str,
        content_type: str,
        sha256: str,
        object_name: str,
        generation: int,
        size: int,
    ) -> bool:
        connection = self.database.connect()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """UPDATE run_operation SET state = 'succeeded', object_name = %s,
                          object_generation = %s, lease_owner = NULL,
                          lease_expires_at = NULL, updated_at = now()
                   WHERE operation_id = %s AND state = 'leased' AND lease_owner = %s
                   RETURNING run_id""",
                (object_name, generation, work.operation_id, work.lease_owner),
            )
            if not cursor.fetchone():
                connection.rollback()
                return False
            cursor.execute(
                """UPDATE media_run SET state = 'queued', source_adapter = %s,
                          content_type = %s, source_sha256 = %s, object_name = %s,
                          object_generation = %s, object_bytes = %s, updated_at = now(),
                          row_version = row_version + 1
                   WHERE run_id = %s AND state = 'fetching' AND NOT cancel_requested""",
                (
                    source_adapter,
                    content_type,
                    sha256,
                    object_name,
                    generation,
                    size,
                    work.run_id,
                ),
            )
            if cursor.rowcount != 1:
                cursor.execute(
                    """UPDATE media_run SET state = 'cancelled', content_type = %s,
                              source_sha256 = %s, object_name = %s,
                              object_generation = %s, object_bytes = %s,
                              completed_at = now(), updated_at = now(),
                              row_version = row_version + 1
                       WHERE run_id = %s AND state = 'fetching' AND cancel_requested""",
                    (
                        content_type,
                        sha256,
                        object_name,
                        generation,
                        size,
                        work.run_id,
                    ),
                )
                if cursor.rowcount == 1:
                    cursor.execute(
                        """INSERT INTO run_cleanup_object
                             (run_id, object_name, object_generation)
                           VALUES (%s,%s,%s)
                           ON CONFLICT (object_name, object_generation) DO NOTHING""",
                        (work.run_id, object_name, generation),
                    )
                    connection.commit()
                    return False
                connection.rollback()
                return False
            cursor.execute(
                """INSERT INTO run_operation
                     (run_id, kind, state, object_name, object_generation)
                   VALUES (%s, 'detect', 'queued', %s, %s)
                   ON CONFLICT (run_id, kind) DO NOTHING""",
                (work.run_id, object_name, generation),
            )
            connection.commit()
            return True
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def fail_fetch(self, work: FetchWork, error_code: str, retryable: bool) -> None:
        connection = self.database.connect()
        cursor = connection.cursor()
        try:
            operation_state = (
                "queued" if retryable and work.attempt_count < 3 else "failed"
            )
            cursor.execute(
                """UPDATE run_operation SET state = %s, last_error_code = %s,
                          lease_owner = NULL, lease_expires_at = NULL, updated_at = now()
                   WHERE operation_id = %s AND lease_owner = %s""",
                (operation_state, error_code, work.operation_id, work.lease_owner),
            )
            if operation_state == "failed":
                cursor.execute(
                    """UPDATE media_run SET state = 'failed', failed_step = 'fetching',
                              retryable = %s, error_code = %s, completed_at = now(),
                              updated_at = now(), row_version = row_version + 1
                       WHERE run_id = %s AND state = 'fetching'""",
                    (retryable, error_code, work.run_id),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()
