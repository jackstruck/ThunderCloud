from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any

from .run_state import RunState


class RunNotFoundError(LookupError):
    pass


class RunConflictError(RuntimeError):
    pass


@dataclass(frozen=True)
class CreatedRun:
    record: dict[str, Any]
    created: bool


def request_fingerprint(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def serialize_run(row) -> dict[str, Any]:
    result = {
        "run_id": str(row[0]),
        "state": str(row[1]),
        "handling_policy": str(row[2]),
        "outcome": row[3],
        "retryable": bool(row[4]),
        "created_at": row[7],
        "updated_at": row[8],
        "expires_at": row[9],
    }
    result["status_url"] = f"/runs/{result['run_id']}"
    if row[5]:
        result["error"] = {"code": str(row[5]), "message": str(row[6])}
    return result


_RUN_COLUMNS = """run_id, state, handling_policy, outcome, retryable, error_code,
                  CASE WHEN error_code IS NULL THEN NULL ELSE 'The run could not complete.' END,
                  created_at, updated_at, expires_at"""


def queue_selection(cursor, run_id, group_ids, assignments):
    """Shared transactional handoff from manual or unattended selection."""
    from .subject_management import save_assignments

    save_assignments(cursor, run_id, group_ids, assignments)
    cursor.execute(
        "UPDATE submission_face_group SET selected = group_id = ANY(%s::uuid[]) WHERE run_id = %s",
        (group_ids, run_id),
    )
    cursor.execute(
        """INSERT INTO run_operation (run_id, kind, state)
           VALUES (%s, 'match', 'queued')
           ON CONFLICT (run_id, kind) DO NOTHING""",
        (run_id,),
    )


class RunRepository:
    """Cloud SQL persistence for the Phase 1 API.

    Authorization is team-wide and is enforced before this layer. Submitter identity is
    used only for idempotency, context, and per-principal active-run quotas.
    """

    def __init__(self, database, active_run_limit: int = 3):
        self.database = database
        self.active_run_limit = active_run_limit

    def create(
        self,
        principal: str,
        idempotency_key: str,
        payload: dict[str, Any],
    ) -> CreatedRun:
        fingerprint = request_fingerprint(payload)
        source = payload["source"]
        state = (
            RunState.FETCHING
            if source["kind"] in {"url", "archive"}
            else RunState.AWAITING_MEDIA
        )
        connection = self.database.connect()
        cursor = connection.cursor()
        try:
            # Serialize the check/insert even when two identical first requests arrive
            # before the unique constraint can expose the winner.
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (principal + "\x1f" + idempotency_key,),
            )
            cursor.execute(
                f"""SELECT {_RUN_COLUMNS}, request_fingerprint
                    FROM media_run
                    WHERE submitter_principal = %s AND idempotency_key = %s
                    FOR UPDATE""",
                (principal, idempotency_key),
            )
            existing = cursor.fetchone()
            if existing:
                if str(existing[10]) != fingerprint:
                    raise RunConflictError(
                        "idempotency key was already used for a different request"
                    )
                connection.commit()
                return CreatedRun(serialize_run(existing), False)
            cursor.execute(
                """SELECT count(*) FROM media_run
                   WHERE submitter_principal = %s
                     AND state NOT IN ('succeeded','failed','cancelled','expired')""",
                (principal,),
            )
            if int(cursor.fetchone()[0]) >= self.active_run_limit:
                raise RunConflictError("active run quota reached")
            run_id = uuid.uuid4()
            cursor.execute(
                f"""INSERT INTO media_run
                    (run_id, submitter_principal, idempotency_key,
                     request_fingerprint, handling_policy, source_kind,
                     source_page_url, content_type, expected_bytes, state, selection_policy,
                     archive_bucket, archive_object_name, archive_object_generation, archive_sha256)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    RETURNING {_RUN_COLUMNS}""",
                (
                    str(run_id),
                    principal,
                    idempotency_key,
                    fingerprint,
                    payload["handling_policy"],
                    source["kind"],
                    source.get("url") or source.get("page_url"),
                    source.get("content_type"),
                    source.get("bytes"),
                    state.value,
                    payload.get("selection_policy", "manual"),
                    source.get("bucket"),
                    source.get("object_name"),
                    source.get("generation"),
                    source.get("sha256"),
                ),
            )
            record = serialize_run(cursor.fetchone())
            operation = "fetch" if state is RunState.FETCHING else None
            if operation:
                cursor.execute(
                    """INSERT INTO run_operation (run_id, kind, state)
                       VALUES (%s, %s, 'queued') ON CONFLICT (run_id, kind) DO NOTHING""",
                    (str(run_id), operation),
                )
            connection.commit()
            return CreatedRun(record, True)
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def get(self, run_id: str) -> dict[str, Any]:
        connection = self.database.connect()
        cursor = connection.cursor()
        try:
            cursor.execute(
                f"SELECT {_RUN_COLUMNS} FROM media_run WHERE run_id = %s",
                (run_id,),
            )
            row = cursor.fetchone()
            if not row:
                raise RunNotFoundError(run_id)
            return serialize_run(row)
        finally:
            connection.rollback()
            cursor.close()
            connection.close()

    def recent(self, principal: str, limit: int = 10) -> list[dict[str, Any]]:
        connection = self.database.connect()
        cursor = connection.cursor()
        try:
            cursor.execute(
                f"""SELECT {_RUN_COLUMNS} FROM media_run
                    WHERE submitter_principal = %s AND expires_at > now()
                      AND state <> 'expired'
                    ORDER BY created_at DESC LIMIT %s""",
                (principal, limit),
            )
            return [serialize_run(row) for row in cursor.fetchall()]
        finally:
            connection.rollback()
            cursor.close()
            connection.close()

    def prepare_upload(self, run_id: str) -> dict[str, Any]:
        connection = self.database.connect()
        cursor = connection.cursor()
        try:
            cursor.execute(
                "SELECT content_type FROM media_run WHERE run_id = %s",
                (run_id,),
            )
            content_type_row = cursor.fetchone()
            if not content_type_row:
                raise RunNotFoundError(run_id)
            extension = {
                "image/jpeg": ".jpg",
                "image/png": ".png",
                "video/mp4": ".mp4",
            }.get(content_type_row[0])
            if extension is None:
                raise RunConflictError("upload content type is invalid")
            object_name = f"submissions-temporary/{run_id}/source{extension}"
            cursor.execute(
                """UPDATE media_run SET object_name = COALESCE(object_name, %s),
                          updated_at = now()
                   WHERE run_id = %s AND source_kind = 'upload'
                     AND state = 'awaiting_media'
                   RETURNING object_name, content_type, expected_bytes""",
                (object_name, run_id),
            )
            row = cursor.fetchone()
            if not row:
                cursor.execute("SELECT 1 FROM media_run WHERE run_id = %s", (run_id,))
                if not cursor.fetchone():
                    raise RunNotFoundError(run_id)
                raise RunConflictError("upload session is not available in this state")
            connection.commit()
            return {
                "object_name": str(row[0]),
                "content_type": str(row[1]),
                "expected_bytes": int(row[2]),
            }
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def finalize_upload(
        self,
        run_id: str,
        *,
        expected_bytes: int,
        expected_sha256: str,
        object_generation: int,
        object_bytes: int,
    ) -> dict[str, Any]:
        if expected_bytes != object_bytes:
            raise RunConflictError("uploaded object size does not match finalization")
        connection = self.database.connect()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """UPDATE media_run SET source_sha256 = %s,
                          object_generation = %s, object_bytes = %s,
                          state = 'queued', updated_at = now(), row_version = row_version + 1
                   WHERE run_id = %s AND source_kind = 'upload'
                     AND state = 'awaiting_media' AND expected_bytes = %s
                   RETURNING run_id""",
                (
                    expected_sha256,
                    object_generation,
                    object_bytes,
                    run_id,
                    expected_bytes,
                ),
            )
            if not cursor.fetchone():
                cursor.execute("SELECT 1 FROM media_run WHERE run_id = %s", (run_id,))
                if not cursor.fetchone():
                    raise RunNotFoundError(run_id)
                raise RunConflictError("upload cannot be finalized in this state")
            cursor.execute(
                """INSERT INTO run_operation (run_id, kind, state, object_name, object_generation)
                   SELECT run_id, 'detect', 'queued', object_name, object_generation
                   FROM media_run WHERE run_id = %s
                   ON CONFLICT (run_id, kind) DO NOTHING""",
                (run_id,),
            )
            connection.commit()
            return self.get(run_id)
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def groups(self, run_id: str) -> tuple[list[dict[str, Any]], int]:
        connection = self.database.connect()
        cursor = connection.cursor()
        try:
            cursor.execute(
                "SELECT state, row_version FROM media_run WHERE run_id = %s",
                (run_id,),
            )
            run = cursor.fetchone()
            if not run:
                raise RunNotFoundError(run_id)
            if run[0] != RunState.AWAITING_FACE_SELECTION.value:
                raise RunConflictError("face groups are not available in this state")
            cursor.execute(
                """SELECT group_id, selected, quality_summary, start_ms, end_ms
                   FROM submission_face_group WHERE run_id = %s
                   ORDER BY local_group_id""",
                (run_id,),
            )
            groups = []
            for group_id, selected, quality, start_ms, end_ms in cursor.fetchall():
                group = {
                    "group_id": str(group_id),
                    "preview_url": f"/api/runs/{run_id}/face-groups/{group_id}/preview",
                    "selected": bool(selected),
                    "quality": quality or {},
                    "time_range_ms": None,
                }
                if start_ms is not None:
                    group["time_range_ms"] = {
                        "start": int(start_ms),
                        "end": int(end_ms),
                    }
                groups.append(group)
            return groups, int(run[1])
        finally:
            connection.rollback()
            cursor.close()
            connection.close()

    def select(
        self, run_id: str, group_ids: list[str], version: int, assignments=None
    ) -> dict[str, Any]:
        connection = self.database.connect()
        cursor = connection.cursor()
        try:
            assignments = assignments or []
            fingerprint = request_fingerprint(
                {"group_ids": sorted(group_ids), "assignments": assignments}
            )
            cursor.execute(
                "SELECT state,selection_fingerprint FROM media_run WHERE run_id=%s FOR UPDATE",
                (run_id,),
            )
            prior = cursor.fetchone()
            if (
                prior
                and prior[1] == fingerprint
                and prior[0] != "awaiting_face_selection"
            ):
                connection.rollback()
                return self.get(run_id)
            cursor.execute(
                """SELECT group_id FROM submission_face_group
                   WHERE run_id = %s AND group_id = ANY(%s::uuid[])""",
                (run_id, group_ids),
            )
            found = {str(row[0]) for row in cursor.fetchall()}
            if found != set(group_ids):
                raise RunConflictError("selection contains an unknown face group")
            cursor.execute(
                """UPDATE media_run SET state = 'matching', selection_fingerprint=%s, row_version = row_version + 1,
                          updated_at = now()
                   WHERE run_id = %s AND state = 'awaiting_face_selection'
                     AND row_version = %s
                   RETURNING run_id""",
                (fingerprint, run_id, version),
            )
            if not cursor.fetchone():
                cursor.execute("SELECT 1 FROM media_run WHERE run_id = %s", (run_id,))
                if not cursor.fetchone():
                    raise RunNotFoundError(run_id)
                raise RunConflictError("selection is stale or no longer editable")
            queue_selection(cursor, run_id, group_ids, assignments)
            connection.commit()
            return self.get(run_id)
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def cancel(self, run_id: str) -> dict[str, Any]:
        connection = self.database.connect()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """SELECT state, EXISTS (
                     SELECT 1 FROM run_operation
                     WHERE run_id = media_run.run_id AND state = 'leased'
                   ) FROM media_run WHERE run_id = %s FOR UPDATE""",
                (run_id,),
            )
            row = cursor.fetchone()
            if not row:
                raise RunNotFoundError(run_id)
            current, worker_active = row
            if current not in {
                "awaiting_media",
                "fetching",
                "queued",
                "detecting",
                "awaiting_face_selection",
            }:
                raise RunConflictError("run can no longer be cancelled")
            cooperative = current == "detecting" or bool(worker_active)
            cursor.execute(
                """UPDATE media_run SET cancel_requested = true,
                          state = CASE WHEN %s THEN state ELSE 'cancelled' END,
                          completed_at = CASE WHEN %s THEN completed_at ELSE now() END,
                          updated_at = now(), row_version = row_version + 1
                   WHERE run_id = %s""",
                (cooperative, cooperative, run_id),
            )
            cursor.execute(
                """UPDATE run_operation SET state = 'failed',
                          last_error_code = 'run_cancelled', lease_owner = NULL,
                          lease_expires_at = NULL, updated_at = now()
                   WHERE run_id = %s AND state = 'queued'
                     AND kind IN ('fetch', 'detect', 'match', 'promotion')""",
                (run_id,),
            )
            if not cooperative:
                cursor.execute(
                    """INSERT INTO run_cleanup_object
                         (run_id, object_name, object_generation)
                       SELECT run_id, object_name, object_generation FROM media_run
                       WHERE run_id = %s AND object_name IS NOT NULL
                         AND object_generation IS NOT NULL
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
            connection.commit()
            return self.get(run_id)
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def retry(self, run_id: str) -> dict[str, Any]:
        connection = self.database.connect()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """UPDATE media_run SET state = failed_step, retryable = false,
                          error_code = NULL, updated_at = now(), row_version = row_version + 1
                   WHERE run_id = %s AND state = 'failed' AND retryable
                     AND failed_step IN ('fetching','queued','detecting','matching')
                   RETURNING failed_step""",
                (run_id,),
            )
            row = cursor.fetchone()
            if not row:
                cursor.execute("SELECT 1 FROM media_run WHERE run_id = %s", (run_id,))
                if not cursor.fetchone():
                    raise RunNotFoundError(run_id)
                raise RunConflictError("run has no retryable failed step")
            kind = {
                "fetching": "fetch",
                "queued": "detect",
                "detecting": "detect",
                "matching": "match",
            }[row[0]]
            cursor.execute(
                """UPDATE run_operation SET state = 'queued', lease_owner = NULL,
                          lease_expires_at = NULL, updated_at = now()
                   WHERE run_id = %s AND kind = %s""",
                (run_id, kind),
            )
            connection.commit()
            return self.get(run_id)
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def results(self, run_id: str) -> dict[str, Any]:
        run = self.get(run_id)
        if run["state"] != RunState.SUCCEEDED.value:
            raise RunConflictError("results are not available in this state")
        connection = self.database.connect()
        cursor = connection.cursor()
        try:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            cursor.execute(
                """SELECT face.group_id, rc.subject_id, rc.rank, rc.similarity,
                          rc.display_name_snapshot, rc.source_count,
                          rc.observation_count, gallery.representative_id,
                          gallery.quality_score, gallery.source_timestamp_ms
                          , rc.page_urls, rc.compared_subject_version,
                          CASE
                            WHEN rc.compared_subject_version IS NOT NULL THEN
                              CASE WHEN current_subject.row_version=rc.compared_subject_version
                                   AND current_subject.merged_into_subject_id IS NULL
                                   THEN 'unchanged' ELSE 'changed' END
                            WHEN EXISTS (
                              SELECT 1 FROM subject_change_event event
                              WHERE event.created_at > rc.created_at
                                AND event.action IN ('edit','combine','move')
                                AND (event.details->>'subject_id'=rc.subject_id::text
                                  OR event.details->>'from_subject_id'=rc.subject_id::text
                                  OR event.details->>'to_subject_id'=rc.subject_id::text
                                  OR event.details->'subject'->>'subject_id'=rc.subject_id::text
                                  OR event.details->'other'->>'subject_id'=rc.subject_id::text
                                  OR event.details->'before_and_partitions'->>'subject_id'=rc.subject_id::text
                                  OR event.details->'shared_identity_subject_ids' ? rc.subject_id::text)
                            ) THEN 'changed'
                            ELSE 'unavailable'
                          END AS subject_version_status
                   FROM submission_face_group face
                   LEFT JOIN run_candidate rc
                     ON rc.run_id = face.run_id AND rc.group_id = face.group_id
                   LEFT JOIN subject current_subject ON current_subject.subject_id=rc.subject_id
                   LEFT JOIN LATERAL (
                     SELECT r.* FROM subject_representative_face r
                     JOIN subject_example e USING(example_id)
                     WHERE e.subject_id=rc.subject_id AND r.active
                     ORDER BY r.quality_score DESC NULLS LAST,r.representative_id LIMIT 5
                   ) gallery ON true
                   WHERE face.run_id = %s AND face.selected
                   ORDER BY rc.group_id, rc.rank, gallery.quality_score DESC NULLS LAST,
                            gallery.representative_id""",
                (run_id,),
            )
            groups: dict[str, dict[str, dict[str, Any]]] = {}
            for row in cursor.fetchall():
                group_id = str(row[0])
                group_candidates = groups.setdefault(group_id, {})
                if row[1] is None:
                    continue
                subject_id = str(row[1])
                candidate = group_candidates.setdefault(
                    subject_id,
                    {
                        "subject_id": subject_id,
                        "display_name": row[4],
                        "rank": int(row[2]),
                        "similarity": float(row[3]),
                        "source_count": int(row[5]),
                        "observation_count": int(row[6]),
                        "page_urls": list(row[10] or []),
                        "compared_subject_version": row[11],
                        "subject_version_status": row[12],
                        "representative_faces": [],
                    },
                )
                if row[7] and len(candidate["representative_faces"]) < 5:
                    candidate["representative_faces"].append(
                        {
                            "representative_id": str(row[7]),
                            "url": f"/api/gallery/faces/{row[7]}",
                            "quality_score": (
                                None if row[8] is None else float(row[8])
                            ),
                            "source_timestamp_ms": row[9],
                        }
                    )
            return {
                "run_id": run_id,
                "groups": [
                    {
                        "group_id": group_id,
                        "candidates": sorted(
                            candidates.values(), key=lambda item: item["rank"]
                        ),
                        "enrollment": self._enrollment_for_group(
                            cursor, run_id, group_id
                        ),
                    }
                    for group_id, candidates in groups.items()
                ],
            }
        finally:
            connection.rollback()
            cursor.close()
            connection.close()

    @staticmethod
    def _enrollment_for_group(cursor, run_id: str, group_id: str):
        cursor.execute(
            """SELECT example.subject_id, enrollment.enrollment_decision, example.source_id
               FROM submission_face_group enrollment
               JOIN subject_example example ON example.submission_group_id=enrollment.group_id
               WHERE enrollment.run_id=%s AND enrollment.group_id=%s AND enrollment.enrolled_at IS NOT NULL""",
            (run_id, group_id),
        )
        row = cursor.fetchone()
        return (
            None
            if row is None
            else {
                "subject_id": str(row[0]),
                "decision": str(row[1]),
                "source_id": str(row[2]),
            }
        )

    def tombstone_source(self, source_id: str, principal: str) -> dict[str, Any]:
        connection = self.database.connect()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """UPDATE source_asset SET deleted_at = COALESCE(deleted_at, now()),
                          deletion_principal = COALESCE(deletion_principal, %s)
                   WHERE source_id = %s AND storage_kind='managed'
                   RETURNING source_id, object_name, object_generation, deleted_at, object_bucket""",
                (principal, source_id),
            )
            row = cursor.fetchone()
            if not row:
                raise RunNotFoundError(source_id)
            cursor.execute(
                """INSERT INTO run_cleanup_object
                     (run_id, object_name, object_generation, object_bucket)
                   SELECT run_id, %s, %s, %s FROM media_run
                   WHERE retained_source_id = %s ORDER BY created_at LIMIT 1
                   ON CONFLICT (object_name, object_generation) DO NOTHING""",
                (row[1], row[2], row[4], source_id),
            )
            connection.commit()
            return {"source_id": str(row[0]), "deleted_at": row[3]}
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()
