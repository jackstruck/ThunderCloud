from __future__ import annotations

import json
import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from .db import Database, Versions
from .manifest import ManifestItem

_ERROR_CODE = re.compile(r"^[A-Z0-9_]{1,64}$")


@dataclass(frozen=True)
class WorkItem:
    work_item_id: str
    rollout_id: str
    manifest_uid: str | None
    source_uri: str
    source_sha256: str
    source_generation: int | None
    source_bytes: int | None
    content_type: str | None
    captured_at: datetime | None
    source_metadata: dict
    application_job_id: str
    attempt_count: int
    max_attempts: int
    lease_owner: str
    staged_uri: str | None
    staged_generation: int | None


class QueueDatabase:
    """Short-transaction durable queue operations for Cloud Run tasks."""

    def __init__(self, database: Database):
        self.database = database

    def _execute(self, operation):
        connection = self.database.connect()
        cursor = connection.cursor()
        try:
            result = operation(cursor)
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def create_rollout(
        self,
        *,
        name: str,
        request_key: str | None,
        image_digest: str,
        versions: Versions,
        creator_principal: str,
        configuration: dict,
        items: Iterable[ManifestItem],
        max_attempts: int = 3,
    ) -> tuple[str, int]:
        if not image_digest.startswith("us-central1-docker.pkg.dev/") or "@sha256:" not in image_digest:
            raise ValueError("image must be an immutable us-central1 Artifact Registry digest")
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        selected = list(items)
        if not selected:
            raise ValueError("a rollout must contain at least one work item")
        if any(not item.sha256 for item in selected):
            raise ValueError("every remotely queued item must have a manifest SHA-256")
        if any(item.bytes is None or item.bytes <= 0 for item in selected):
            raise ValueError("every remotely queued item must have a positive byte size")
        rollout_id = str(uuid.uuid4())

        def operation(cursor):
            cursor.execute(
                """INSERT INTO processing_rollout
                   (rollout_id,name,request_key,image_digest,worker_version,detector_version,
                    embedding_model_version,threshold_version,matching_enabled,status,
                    creator_principal,configuration)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,true,'queued',%s,%s::jsonb)
                   ON CONFLICT (request_key) DO NOTHING
                   RETURNING rollout_id""",
                (
                    rollout_id, name, request_key, image_digest, versions.worker,
                    versions.detector, versions.embedding, versions.threshold,
                    creator_principal, json.dumps(configuration),
                ),
            )
            inserted = cursor.fetchone()
            if inserted is None:
                if request_key is None:
                    raise RuntimeError("rollout insert unexpectedly returned no row")
                cursor.execute(
                    "SELECT rollout_id,requested_count FROM processing_rollout WHERE request_key=%s",
                    (request_key,),
                )
                existing = cursor.fetchone()
                if existing is None:
                    raise RuntimeError("idempotent rollout lookup failed")
                return str(existing[0]), int(existing[1])
            effective_id = str(inserted[0])
            for item in selected:
                metadata = {
                    "uid": item.uid,
                    "generation": item.generation,
                    "bytes": item.bytes,
                    "content_type": item.content_type,
                    "manifest_timestamp": item.timestamp,
                    **item.source,
                }
                cursor.execute(
                    """INSERT INTO processing_work_item
                       (rollout_id,manifest_uid,source_uri,source_sha256,source_generation,
                        source_bytes,content_type,captured_at,source_metadata,
                        application_job_id,max_attempts)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s)
                       ON CONFLICT (rollout_id,source_uri,source_sha256) DO NOTHING""",
                    (
                        effective_id, item.uid, item.object_uri, item.sha256,
                        int(item.generation) if item.generation else None, item.bytes,
                        item.content_type, item.timestamp, json.dumps(metadata),
                        str(uuid.uuid4()), max_attempts,
                    ),
                )
            cursor.execute(
                """UPDATE processing_rollout SET requested_count=(
                       SELECT count(*) FROM processing_work_item WHERE rollout_id=%s
                   ) WHERE rollout_id=%s RETURNING requested_count""",
                (effective_id, effective_id),
            )
            return effective_id, int(cursor.fetchone()[0])

        return self._execute(operation)

    def start_rollout(self, rollout_id: str) -> None:
        def operation(cursor):
            cursor.execute(
                """UPDATE processing_rollout SET status='running', started_at=COALESCE(started_at,now())
                   WHERE rollout_id=%s AND status='queued' RETURNING rollout_id""",
                (rollout_id,),
            )
            if cursor.fetchone() is None:
                raise ValueError("rollout is missing or is not startable")

        self._execute(operation)

    def record_execution(self, rollout_id: str, execution_name: str) -> None:
        if not execution_name or "/" in execution_name:
            raise ValueError("invalid Cloud Run execution name")

        def operation(cursor):
            cursor.execute(
                """UPDATE processing_rollout SET cloud_run_execution_name=%s
                   WHERE rollout_id=%s AND status='running'
                     AND cloud_run_execution_name IS NULL RETURNING rollout_id""",
                (execution_name, rollout_id),
            )
            if cursor.fetchone() is None:
                raise RuntimeError("rollout execution is already recorded or not running")

        self._execute(operation)

    def launch_failed(self, rollout_id: str) -> None:
        def operation(cursor):
            cursor.execute(
                """UPDATE processing_rollout SET status='queued',started_at=NULL
                   WHERE rollout_id=%s AND status='running' AND NOT EXISTS (
                     SELECT 1 FROM processing_work_item
                     WHERE rollout_id=%s AND state NOT IN ('pending','retry')
                   ) RETURNING rollout_id""",
                (rollout_id, rollout_id),
            )

        self._execute(operation)

    def claim(
        self,
        rollout_id: str,
        lease_minutes: int = 20,
        max_source_bytes: int | None = None,
    ) -> WorkItem | None:
        if lease_minutes < 1:
            raise ValueError("lease_minutes must be positive")
        owner = str(uuid.uuid4())

        def operation(cursor):
            cursor.execute(
                """WITH candidate AS (
                       SELECT work_item_id FROM processing_work_item
                       WHERE rollout_id=%s AND attempt_count < max_attempts
                         AND (%s::bigint IS NULL OR source_bytes IS NULL OR source_bytes <= %s)
                         AND (state IN ('pending','retry')
                              OR (state='leased' AND lease_expires_at < now()))
                         AND EXISTS (SELECT 1 FROM processing_rollout r
                                     WHERE r.rollout_id=%s AND r.status='running')
                       ORDER BY work_item_id FOR UPDATE SKIP LOCKED LIMIT 1
                   )
                   UPDATE processing_work_item w SET
                       state='leased', attempt_count=attempt_count+1, lease_owner=%s,
                       lease_acquired_at=now(), heartbeat_at=now(),
                       lease_expires_at=now()+(%s * interval '1 minute'), updated_at=now()
                   FROM candidate WHERE w.work_item_id=candidate.work_item_id
                   RETURNING w.work_item_id,w.rollout_id,w.manifest_uid,w.source_uri,
                     w.source_sha256,w.source_generation,w.source_bytes,w.content_type,
                     w.captured_at,w.source_metadata,w.application_job_id,w.attempt_count,
                     w.max_attempts,w.staged_uri,w.staged_generation""",
                (
                    rollout_id,
                    max_source_bytes,
                    max_source_bytes,
                    rollout_id,
                    owner,
                    lease_minutes,
                ),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return WorkItem(
                *(str(value) if index in {0, 1, 10} else value for index, value in enumerate(row[:13])),
                lease_owner=owner,
                staged_uri=row[13],
                staged_generation=row[14],
            )

        return self._execute(operation)

    def expire_exhausted_leases(self, rollout_id: str) -> int:
        def operation(cursor):
            cursor.execute(
                """UPDATE processing_work_item SET state='dead_letter',
                     last_error_code='TASK_LEASE_EXPIRED',updated_at=now(),
                     lease_owner=NULL,lease_expires_at=NULL
                   WHERE rollout_id=%s AND state='leased' AND lease_expires_at < now()
                     AND attempt_count >= max_attempts
                   RETURNING work_item_id""",
                (rollout_id,),
            )
            return len(cursor.fetchall())

        return self._execute(operation)

    def heartbeat(self, item: WorkItem, lease_minutes: int = 20) -> bool:
        def operation(cursor):
            cursor.execute(
                """UPDATE processing_work_item SET heartbeat_at=now(),
                     lease_expires_at=now()+(%s * interval '1 minute'),updated_at=now()
                   WHERE work_item_id=%s AND state='leased' AND lease_owner=%s
                   RETURNING work_item_id""",
                (lease_minutes, item.work_item_id, item.lease_owner),
            )
            return cursor.fetchone() is not None

        return self._execute(operation)

    def record_staging(self, item: WorkItem, uri: str, generation: int) -> None:
        self._owned_update(
            item,
            "staged_uri=%s, staged_generation=%s, updated_at=now()",
            (uri, generation),
        )

    def succeed(self, item: WorkItem) -> None:
        self._owned_update(
            item,
            """state='succeeded',completed_at=now(),updated_at=now(),last_error_code=NULL,
               lease_owner=NULL,lease_expires_at=NULL""",
            (),
        )
        self.refresh_rollout(item.rollout_id)

    def fail(self, item: WorkItem, error_code: str, retryable: bool) -> str:
        if not _ERROR_CODE.fullmatch(error_code):
            raise ValueError("error code must be a sanitized uppercase identifier")
        state = "retry" if retryable and item.attempt_count < item.max_attempts else "dead_letter"
        self._owned_update(
            item,
            """state=%s,last_error_code=%s,updated_at=now(),lease_owner=NULL,
               lease_expires_at=NULL""",
            (state, error_code),
        )
        self.refresh_rollout(item.rollout_id)
        return state

    def _owned_update(self, item: WorkItem, assignments: str, values: tuple) -> None:
        def operation(cursor):
            cursor.execute(
                f"""UPDATE processing_work_item SET {assignments}
                    WHERE work_item_id=%s AND state='leased' AND lease_owner=%s
                    RETURNING work_item_id""",  # assignments are internal constants only
                (*values, item.work_item_id, item.lease_owner),
            )
            if cursor.fetchone() is None:
                raise RuntimeError("work-item lease is no longer owned")

        self._execute(operation)

    def refresh_rollout(self, rollout_id: str) -> dict:
        def operation(cursor):
            cursor.execute(
                """SELECT count(*) FILTER (WHERE state='succeeded'),
                          count(*) FILTER (WHERE state='retry'),
                          count(*) FILTER (WHERE state='dead_letter'),
                          count(*) FILTER (WHERE state IN ('pending','retry','leased'))
                   FROM processing_work_item WHERE rollout_id=%s""",
                (rollout_id,),
            )
            succeeded, retryable, dead, active = (int(v) for v in cursor.fetchone())
            terminal = active == 0
            status = "failed" if terminal and dead else "succeeded" if terminal else "running"
            cursor.execute(
                """UPDATE processing_rollout SET succeeded_count=%s,retryable_count=%s,
                     dead_letter_count=%s,status=%s,
                     completed_at=CASE WHEN %s THEN now() ELSE NULL END
                   WHERE rollout_id=%s
                   RETURNING requested_count""",
                (succeeded, retryable, dead, status, terminal, rollout_id),
            )
            row = cursor.fetchone()
            if row is None:
                raise ValueError("rollout does not exist")
            return {"requested": int(row[0]), "succeeded": succeeded, "retryable": retryable,
                    "dead_letter": dead, "active": active, "status": status}

        return self._execute(operation)

    def rollout_status(self, rollout_id: str) -> dict:
        def operation(cursor):
            cursor.execute(
                """SELECT rollout_id,name,image_digest,status,requested_count,
                          succeeded_count,retryable_count,dead_letter_count,
                          matching_enabled,threshold_version,cloud_run_execution_name,
                          created_at,started_at,completed_at
                   FROM processing_rollout WHERE rollout_id=%s""",
                (rollout_id,),
            )
            row = cursor.fetchone()
            if row is None:
                raise ValueError("rollout does not exist")
            keys = (
                "rollout_id", "name", "image_digest", "status", "requested_count",
                "succeeded_count", "retryable_count", "dead_letter_count",
                "matching_enabled", "threshold_version", "cloud_run_execution_name",
                "created_at", "started_at", "completed_at",
            )
            return {
                key: (str(value) if isinstance(value, (uuid.UUID, datetime)) else value)
                for key, value in zip(keys, row)
            }

        return self._execute(operation)

    def assert_worker_contract(
        self, rollout_id: str, versions: Versions, matching_enabled: bool
    ) -> None:
        def operation(cursor):
            cursor.execute(
                """SELECT worker_version,detector_version,embedding_model_version,
                          threshold_version,matching_enabled
                   FROM processing_rollout WHERE rollout_id=%s""",
                (rollout_id,),
            )
            row = cursor.fetchone()
            expected = (
                versions.worker,
                versions.detector,
                versions.embedding,
                versions.threshold,
                matching_enabled,
            )
            if row is None:
                raise ValueError("rollout does not exist")
            if tuple(row) != expected:
                raise RuntimeError("worker configuration does not match rollout contract")

        self._execute(operation)

    def reconcile(self, rollout_id: str) -> dict:
        summary = self.refresh_rollout(rollout_id)

        def operation(cursor):
            cursor.execute(
                """SELECT count(*) FROM processing_work_item w
                   JOIN processing_rollout r ON r.rollout_id=w.rollout_id
                   WHERE w.rollout_id=%s AND w.state='succeeded' AND NOT EXISTS (
                     SELECT 1 FROM source_asset s JOIN processing_job j ON j.source_id=s.source_id
                     WHERE s.external_source_ref=w.source_uri
                       AND s.source_sha256=w.source_sha256
                       AND j.status='succeeded'
                       AND j.worker_version=r.worker_version
                       AND j.detector_version=r.detector_version
                       AND j.embedding_model_version=r.embedding_model_version
                       AND j.threshold_version=r.threshold_version
                   )""",
                (rollout_id,),
            )
            missing_commits = int(cursor.fetchone()[0])
            cursor.execute(
                """SELECT source_uri,source_generation,source_bytes,staged_uri,staged_generation
                   FROM processing_work_item WHERE rollout_id=%s AND state='succeeded'""",
                (rollout_id,),
            )
            objects = cursor.fetchall()
            return missing_commits, objects

        missing_commits, objects = self._execute(operation)
        return {
            **summary,
            "missing_committed_results": missing_commits,
            "objects": objects,
        }
