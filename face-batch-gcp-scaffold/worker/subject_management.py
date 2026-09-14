from __future__ import annotations

import hashlib
import json
import math
import uuid
from contextlib import contextmanager

from .db import pgvector


class SubjectError(ValueError):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def subject_uuid(value):
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as error:
        raise SubjectError(
            422, "invalid_subject_id", "Enter a valid subject UUID."
        ) from error


def normalized(values):
    values = [float(x) for x in values]
    norm = math.sqrt(sum(x * x for x in values))
    if len(values) != 512 or not math.isfinite(norm) or norm <= 1e-12:
        raise SubjectError(
            409,
            "invalid_embedding",
            "The examples do not form a valid subject embedding.",
        )
    return [x / norm for x in values]


def resolve_subject(cursor, subject_id):
    requested = subject_uuid(subject_id)
    current, visited = requested, set()
    while current not in visited:
        visited.add(current)
        cursor.execute(
            "SELECT merged_into_subject_id FROM subject WHERE subject_id=%s", (current,)
        )
        row = cursor.fetchone()
        if row is None:
            raise SubjectError(404, "subject_not_found", "Subject not found.")
        if row[0] is None:
            return current
        current = str(row[0])
    raise SubjectError(409, "subject_redirect_cycle", "Subject redirects need repair.")


def recalculate_subjects(cursor, subject_ids):
    ids = sorted(set(map(str, subject_ids)))
    if not ids:
        return
    cursor.execute(
        "SELECT subject_id,model_version FROM subject WHERE subject_id=ANY(%s::uuid[]) ORDER BY subject_id FOR UPDATE",
        (ids,),
    )
    models = {str(sid): model for sid, model in cursor.fetchall()}
    if set(models) != set(ids):
        raise SubjectError(404, "subject_not_found", "Subject not found.")
    totals = {sid: [0.0] * 512 for sid in ids}
    counts = dict.fromkeys(ids, 0)
    cursor.execute(
        """SELECT e.subject_id,e.embedding::text,
                  e.model_version
           FROM subject_example e
           WHERE e.subject_id=ANY(%s::uuid[]) ORDER BY e.subject_id,e.example_id""",
        (ids,),
    )
    contributing_models = {}
    for sid, embedding, version in cursor.fetchall():
        sid = str(sid)
        if sid in contributing_models and version != contributing_models[sid]:
            raise SubjectError(
                409,
                "model_mismatch",
                "Subjects and examples must use the same recognition model.",
            )
        contributing_models[sid] = version
        vector = normalized(json.loads(embedding))
        totals[sid] = [a + b for a, b in zip(totals[sid], vector, strict=True)]
        counts[sid] += 1
    aggregates = [
        pgvector(normalized(totals[sid])) if counts[sid] else None for sid in ids
    ]
    cursor.execute(
        """UPDATE subject s SET canonical_embedding=data.embedding::vector,
           sample_count=data.samples,model_version=data.model,row_version=s.row_version+1,updated_at=now()
           FROM unnest(%s::uuid[],%s::text[],%s::int[],%s::text[]) AS data(id,embedding,samples,model)
           WHERE s.subject_id=data.id""",
        (
            ids,
            aggregates,
            [counts[sid] for sid in ids],
            [contributing_models.get(sid, models[sid]) for sid in ids],
        ),
    )


class SubjectManagement:
    def __init__(self, database):
        self.database = database

    @contextmanager
    def transaction(self, write=False):
        connection = self.database.connect()
        cursor = connection.cursor()
        try:
            if write:
                # Same lock as enrollment: serializes cross-subject edits and redirects.
                cursor.execute("SELECT pg_advisory_xact_lock(8675309)")
            else:
                cursor.execute(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
                )
            yield cursor
            connection.commit() if write else connection.rollback()
        except Exception as error:
            connection.rollback()
            if (
                error.args
                and isinstance(error.args[0], dict)
                and error.args[0].get("C") == "23505"
            ):
                raise SubjectError(
                    409,
                    "external_reference_conflict",
                    "That external identity reference is already in use.",
                ) from error
            raise
        finally:
            cursor.close()
            connection.close()

    def _detail(self, cursor, requested):
        requested = subject_uuid(requested)
        sid = resolve_subject(cursor, requested)
        cursor.execute(
            """SELECT s.subject_id,s.identity_id,s.model_version,s.sample_count,s.row_version,
                                 i.display_name,i.external_identity_ref,i.row_version,s.created_at,s.updated_at,
                                 (SELECT count(DISTINCT source_id) FROM subject_example WHERE subject_id=s.subject_id),
                                 (SELECT count(*) FROM subject_example WHERE subject_id=s.subject_id)
                          FROM subject s LEFT JOIN identity i USING(identity_id) WHERE s.subject_id=%s""",
            (sid,),
        )
        row = cursor.fetchone()
        result = dict(
            zip(
                (
                    "subject_id",
                    "identity_id",
                    "model_version",
                    "sample_count",
                    "version",
                    "display_name",
                    "external_identity_ref",
                    "identity_version",
                    "created_at",
                    "updated_at",
                    "source_count",
                    "observation_count",
                ),
                row,
            )
        )
        result["subject_id"] = str(result["subject_id"])
        result["identity_id"] = (
            None if result["identity_id"] is None else str(result["identity_id"])
        )
        result["requested_subject_id"] = requested
        result["resolved_from"] = requested if requested != sid else None
        result["example_count"] = result["observation_count"]
        cursor.execute(
            """SELECT r.representative_id,r.quality_score,r.source_timestamp_ms
                          FROM subject_representative_face r JOIN subject_example e USING(example_id)
                          WHERE e.subject_id=%s AND r.active
                          ORDER BY r.quality_score DESC NULLS LAST,r.representative_id LIMIT 5""",
            (sid,),
        )
        result["representative_faces"] = [
            {
                "representative_id": str(r[0]),
                "url": f"/api/gallery/faces/{r[0]}",
                "quality_score": r[1],
                "source_timestamp_ms": r[2],
            }
            for r in cursor.fetchall()
        ]
        cursor.execute(
            """SELECT DISTINCT sa.source_page_url AS url
                          FROM subject_example e JOIN source_asset sa USING(source_id)
                          WHERE e.subject_id=%s AND sa.source_page_url IS NOT NULL ORDER BY url""",
            (sid,),
        )
        result["page_urls"] = [r[0] for r in cursor.fetchall()]
        cursor.execute(
            "SELECT subject_id FROM subject WHERE identity_id=%s AND merged_into_subject_id IS NULL ORDER BY subject_id",
            (result["identity_id"],),
        )
        result["shared_identity_subject_ids"] = [str(r[0]) for r in cursor.fetchall()]
        return result

    def subject(self, subject_id):
        with self.transaction() as cursor:
            return self._detail(cursor, subject_id)

    def subjects(self, q="", after=None, limit=24, multiple_sources=False, sort="id",
                 shared_source=False, source_id=None):
        if (
            not isinstance(q, str)
            or len(q) > 200
            or type(limit) is not int
            or not 1 <= limit <= 100
            or type(multiple_sources) is not bool
            or type(shared_source) is not bool
            or sort not in {"id", "sources"}
        ):
            raise SubjectError(
                422,
                "invalid_search",
                "Use a valid search, source filter, sort, and page size from 1 to 100.",
            )
        q = q.strip()
        source_id = subject_uuid(source_id) if source_id else None
        after_count, after_id = None, None
        if after:
            if sort == "sources":
                try:
                    count, raw_id = after.split(":", 1)
                    after_count = int(count)
                    if after_count < 0:
                        raise ValueError()
                    after_id = subject_uuid(raw_id)
                except (ValueError, AttributeError):
                    raise SubjectError(
                        422, "invalid_cursor", "Refresh the search to reset pagination."
                    ) from None
            else:
                after_id = subject_uuid(after)
        with self.transaction() as cursor:
            try:
                exact = str(uuid.UUID(q))
            except ValueError:
                exact = None
            if exact:
                detail = self._detail(cursor, exact)
                cursor.execute("""SELECT EXISTS (
                    SELECT 1 FROM subject_example e WHERE e.subject_id=%s
                    AND (%s::uuid IS NULL OR e.source_id=%s::uuid)
                    AND (NOT %s OR EXISTS (
                        SELECT 1 FROM subject_example other JOIN subject s ON s.subject_id=other.subject_id
                        WHERE other.source_id=e.source_id AND other.subject_id<>e.subject_id
                          AND s.merged_into_subject_id IS NULL)))""",
                    (detail['subject_id'], source_id, source_id, shared_source))
                included = cursor.fetchone()[0] if source_id or shared_source else True
                return {
                    "subjects": [detail]
                    if included and (not multiple_sources or detail["source_count"] > 1)
                    else [],
                    "next_cursor": None,
                }
            pattern = (
                "%"
                + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                + "%"
            )
            order = "sources DESC,subject_id" if sort == "sources" else "subject_id"
            cursor.execute(
                f"""WITH counts AS (
                      SELECT subject_id,count(DISTINCT source_id) AS sources FROM subject_example GROUP BY subject_id
                    ), listed AS (
                      SELECT s.subject_id,COALESCE(c.sources,0) AS sources
                      FROM subject s LEFT JOIN identity i USING(identity_id) LEFT JOIN counts c USING(subject_id)
                      WHERE s.merged_into_subject_id IS NULL
                        AND (%s='' OR lower(i.display_name) LIKE lower(%s))
                        AND (NOT %s OR COALESCE(c.sources,0)>1)
                        AND (%s::uuid IS NULL OR EXISTS (
                            SELECT 1 FROM subject_example e WHERE e.subject_id=s.subject_id AND e.source_id=%s::uuid))
                        AND (NOT %s OR EXISTS (
                            SELECT 1 FROM subject_example e JOIN subject_example other ON other.source_id=e.source_id
                            JOIN subject other_subject ON other_subject.subject_id=other.subject_id
                            WHERE e.subject_id=s.subject_id AND other.subject_id<>s.subject_id
                              AND other_subject.merged_into_subject_id IS NULL))
                    ) SELECT subject_id,sources FROM listed
                    WHERE (%s::uuid IS NULL OR
                      (%s='id' AND subject_id>%s::uuid) OR
                      (%s='sources' AND (sources<%s OR (sources=%s AND subject_id>%s::uuid))))
                    ORDER BY {order} LIMIT %s""",
                (
                    q,
                    pattern,
                    multiple_sources,
                    source_id,
                    source_id,
                    shared_source,
                    after_id,
                    sort,
                    after_id,
                    sort,
                    after_count,
                    after_count,
                    after_id,
                    limit + 1,
                ),
            )
            rows = cursor.fetchall()
            next_cursor = None
            if len(rows) > limit:
                sid, count = rows[limit - 1]
                next_cursor = f"{count}:{sid}" if sort == "sources" else str(sid)
            return {
                "subjects": self._summaries(
                    cursor, [str(row[0]) for row in rows[:limit]]
                ),
                "next_cursor": next_cursor,
            }

    def _summaries(self, cursor, ids):
        if not ids:
            return []
        cursor.execute(
            """SELECT s.subject_id,s.identity_id,s.model_version,s.sample_count,s.row_version,
                                 i.display_name,i.external_identity_ref,i.row_version,
                                 (SELECT count(DISTINCT source_id) FROM subject_example WHERE subject_id=s.subject_id),
                                 (SELECT count(*) FROM subject_example WHERE subject_id=s.subject_id)
                          FROM subject s LEFT JOIN identity i USING(identity_id)
                          WHERE s.subject_id=ANY(%s::uuid[])""",
            (ids,),
        )
        result = {}
        fields = (
            "subject_id",
            "identity_id",
            "model_version",
            "sample_count",
            "version",
            "display_name",
            "external_identity_ref",
            "identity_version",
            "source_count",
            "example_count",
        )
        for row in cursor.fetchall():
            item = dict(zip(fields, row, strict=True))
            item["subject_id"] = str(item["subject_id"])
            item["identity_id"] = (
                str(item["identity_id"]) if item["identity_id"] else None
            )
            item["observation_count"] = item["example_count"]
            item["representative_faces"], item["page_urls"] = [], []
            result[item["subject_id"]] = item
        cursor.execute(
            """SELECT subject_id,representative_id,quality_score,source_timestamp_ms FROM (
                          SELECT e.subject_id,r.representative_id,r.quality_score,r.source_timestamp_ms,
                            row_number() OVER (PARTITION BY e.subject_id ORDER BY r.quality_score DESC NULLS LAST,r.representative_id) AS position
                          FROM subject_representative_face r JOIN subject_example e USING(example_id)
                          WHERE e.subject_id=ANY(%s::uuid[]) AND r.active) ranked
                          WHERE position<=5 ORDER BY subject_id,position""",
            (ids,),
        )
        for sid, rid, quality, timestamp in cursor.fetchall():
            result[str(sid)]["representative_faces"].append(
                {
                    "representative_id": str(rid),
                    "url": f"/api/gallery/faces/{rid}",
                    "quality_score": quality,
                    "source_timestamp_ms": timestamp,
                }
            )
        cursor.execute(
            """SELECT DISTINCT e.subject_id,sa.source_page_url AS url
                          FROM subject_example e JOIN source_asset sa USING(source_id)
                          WHERE e.subject_id=ANY(%s::uuid[]) AND sa.source_page_url IS NOT NULL
                          ORDER BY e.subject_id,url""",
            (ids,),
        )
        for sid, url in cursor.fetchall():
            result[str(sid)]["page_urls"].append(url)
        return [result[sid] for sid in ids]

    def potential_matches(self, subject_id, dismissed=False):
        sid = subject_uuid(subject_id)
        with self.transaction() as cursor:
            cursor.execute(
                "SELECT row_version,merged_into_subject_id FROM subject WHERE subject_id=%s",
                (sid,),
            )
            subject = cursor.fetchone()
            if subject is None:
                raise SubjectError(404, "subject_not_found", "Subject not found.")
            if subject[1] is not None:
                raise SubjectError(
                    409,
                    "subject_merged",
                    "This subject was combined. Open the surviving subject.",
                )
            cursor.execute(
                """SELECT candidate.subject_id,
                          1 - (candidate.canonical_embedding <=> origin.canonical_embedding) AS similarity
                   FROM subject origin JOIN subject candidate
                     ON candidate.model_version=origin.model_version
                   WHERE origin.subject_id=%s AND candidate.subject_id<>origin.subject_id
                     AND origin.canonical_embedding IS NOT NULL AND origin.sample_count>0
                     AND candidate.canonical_embedding IS NOT NULL AND candidate.sample_count>0
                     AND candidate.merged_into_subject_id IS NULL
                     AND EXISTS (
                       SELECT 1 FROM subject_suggestion_dismissal d
                       WHERE d.subject_low=LEAST(origin.subject_id,candidate.subject_id)
                         AND d.subject_high=GREATEST(origin.subject_id,candidate.subject_id)
                         AND d.low_version=CASE WHEN origin.subject_id<candidate.subject_id
                             THEN origin.row_version ELSE candidate.row_version END
                         AND d.high_version=CASE WHEN origin.subject_id<candidate.subject_id
                             THEN candidate.row_version ELSE origin.row_version END
                         AND d.restored_at IS NULL
                     ) = %s
                   ORDER BY similarity DESC, candidate.subject_id LIMIT 10""",
                (sid, dismissed),
            )
            ranked = [(str(row[0]), float(row[1])) for row in cursor.fetchall()]
            summaries = self._summaries(cursor, [candidate for candidate, _ in ranked])
            for summary, (_, similarity) in zip(summaries, ranked, strict=True):
                summary["similarity"] = similarity
            return {
                "subject_id": sid,
                "version": subject[0],
                "dismissed": dismissed,
                "candidates": summaries,
            }

    def suggestion_dismissal(
        self, subject_id, candidate_id, data, actor, restore=False
    ):
        sid, candidate = subject_uuid(subject_id), subject_uuid(candidate_id)
        if sid == candidate or set(data) != {
            "operation_id",
            "version",
            "target_version",
        }:
            raise SubjectError(
                422,
                "invalid_dismissal",
                "Provide two different subjects, their versions, and an operation ID.",
            )
        low, high = sorted([sid, candidate])
        action = "restore_suggestion" if restore else "dismiss_suggestion"
        with self.transaction(write=True) as cursor:
            operation, fingerprint, replay = self._operation(
                cursor, sid, action, {**data, "candidate_id": candidate}, actor
            )
            cursor.execute(
                """SELECT subject_id,row_version,merged_into_subject_id,model_version,
                          canonical_embedding IS NOT NULL AND sample_count>0
                   FROM subject WHERE subject_id=ANY(%s::uuid[]) ORDER BY subject_id FOR UPDATE""",
                ([low, high],),
            )
            subjects = {str(row[0]): row[1:] for row in cursor.fetchall()}
            if len(subjects) != 2:
                raise SubjectError(404, "subject_not_found", "Subject not found.")
            for key, expected in [
                (sid, data["version"]),
                (candidate, data["target_version"]),
            ]:
                self._version(subjects[key][0], expected)
                if subjects[key][1] is not None:
                    raise SubjectError(
                        409,
                        "subject_merged",
                        "A subject was combined. Refresh the comparison.",
                    )
            if subjects[sid][2] != subjects[candidate][2] or not all(
                row[3] for row in subjects.values()
            ):
                raise SubjectError(
                    409,
                    "incompatible_subjects",
                    "These subjects no longer have compatible representations.",
                )
            if replay is not None:
                return replay
            versions = [subjects[low][0], subjects[high][0]]
            cursor.execute(
                "SELECT low_version,high_version,dismissed_by,dismissed_at,restored_by,restored_at FROM subject_suggestion_dismissal WHERE subject_low=%s AND subject_high=%s",
                (low, high),
            )
            previous = cursor.fetchone()
            if restore:
                cursor.execute(
                    """UPDATE subject_suggestion_dismissal SET restored_by=%s,restored_at=now()
                       WHERE subject_low=%s AND subject_high=%s AND restored_at IS NULL""",
                    (actor, low, high),
                )
            else:
                cursor.execute(
                    """INSERT INTO subject_suggestion_dismissal
                       (subject_low,subject_high,low_version,high_version,dismissed_by)
                       VALUES (%s,%s,%s,%s,%s)
                       ON CONFLICT (subject_low,subject_high) DO UPDATE
                       SET low_version=EXCLUDED.low_version,high_version=EXCLUDED.high_version,
                           dismissed_by=EXCLUDED.dismissed_by,dismissed_at=now(),restored_by=NULL,restored_at=NULL
                       WHERE subject_suggestion_dismissal.low_version<>EXCLUDED.low_version
                          OR subject_suggestion_dismissal.high_version<>EXCLUDED.high_version
                          OR subject_suggestion_dismissal.restored_at IS NOT NULL""",
                    (low, high, *versions, actor),
                )
            result = {
                "subject_id": sid,
                "candidate_id": candidate,
                "version": data["version"],
                "target_version": data["target_version"],
                "dismissed": not restore,
            }
            self._record(
                cursor,
                operation,
                actor,
                action,
                fingerprint,
                {"subject_low": low, "subject_high": high, "previous": previous},
                result,
            )
            return result

    def browse_sources(self, q="", after=None, limit=24, multiple_subjects=False):
        if not isinstance(q, str) or len(q) > 200 or type(limit) is not int or not 1 <= limit <= 100 or type(multiple_subjects) is not bool:
            raise SubjectError(422, "invalid_search", "Use a valid source search and page size from 1 to 100.")
        after = subject_uuid(after) if after else None
        pattern = "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        with self.transaction() as cursor:
            cursor.execute("""SELECT e.source_id,sa.source_page_url,count(DISTINCT e.subject_id),count(*)
                FROM subject_example e JOIN subject s USING(subject_id) JOIN source_asset sa USING(source_id)
                WHERE s.merged_into_subject_id IS NULL
                  AND (%s::uuid IS NULL OR e.source_id>%s::uuid)
                  AND (e.source_id::text ILIKE %s OR sa.source_page_url ILIKE %s)
                GROUP BY e.source_id,sa.source_page_url
                HAVING NOT %s OR count(DISTINCT e.subject_id)>1
                ORDER BY e.source_id LIMIT %s""", (after, after, pattern, pattern, multiple_subjects, limit + 1))
            rows = cursor.fetchall()
            return {"sources": [{"source_id": str(r[0]), "page_url": r[1], "subject_count": r[2], "example_count": r[3]} for r in rows[:limit]],
                    "next_cursor": str(rows[limit - 1][0]) if len(rows) > limit else None}

    def sources(self, subject_id):
        with self.transaction() as cursor:
            sid = resolve_subject(cursor, subject_id)
            cursor.execute(
                "SELECT row_version FROM subject WHERE subject_id=%s", (sid,)
            )
            version = cursor.fetchone()[0]
            cursor.execute(
                """SELECT e.source_id,count(*),sa.source_page_url,
                              (SELECT count(DISTINCT other.subject_id) FROM subject_example other
                               JOIN subject current_subject ON current_subject.subject_id=other.subject_id
                               WHERE other.source_id=e.source_id AND current_subject.merged_into_subject_id IS NULL)
                              FROM subject_example e JOIN source_asset sa USING(source_id)
                              WHERE e.subject_id=%s GROUP BY e.source_id,sa.source_page_url ORDER BY e.source_id""",
                (sid,),
            )
            return {
                "subject_id": sid,
                "version": version,
                "sources": [
                    {"source_id": str(r[0]), "example_count": r[1], "page_url": r[2], "subject_count": r[3]}
                    for r in cursor.fetchall()
                ],
            }

    def examples(self, subject_id, after=None, limit=30, source_id=None, with_previews=False):
        if not isinstance(limit, int) or not 1 <= limit <= 100:
            raise SubjectError(
                422, "invalid_page_size", "Page size must be from 1 to 100."
            )
        if type(with_previews) is not bool:
            raise SubjectError(422, "invalid_search", "Use true or false for the preview filter.")
        after = subject_uuid(after) if after else None
        source_id = subject_uuid(source_id) if source_id else None
        with self.transaction() as cursor:
            sid = resolve_subject(cursor, subject_id)
            source_time_range = None
            if source_id:
                cursor.execute("SELECT min(start_ms),max(end_ms) FROM subject_example WHERE subject_id=%s AND source_id=%s", (sid, source_id))
                span = cursor.fetchone()
                source_time_range = {"start_ms": span[0], "end_ms": span[1]}
            cursor.execute(
                """SELECT e.example_id,e.source_id,e.model_version,e.start_ms,e.end_ms,e.quality_score,
                                     ft.processing_job_id,g.run_id,sa.content_type,
                                     sa.source_page_url,
                                     (SELECT r.representative_id FROM subject_representative_face r
                                      WHERE r.example_id=e.example_id AND r.active ORDER BY r.representative_id LIMIT 1),
                                     e.face_track_id,e.submission_group_id
                              FROM subject_example e LEFT JOIN face_track ft ON ft.track_id=e.face_track_id
                              LEFT JOIN submission_face_group g ON g.group_id=e.submission_group_id
                              JOIN source_asset sa ON sa.source_id=e.source_id
                              WHERE e.subject_id=%s AND (%s::uuid IS NULL OR e.example_id>%s::uuid)
                                AND (%s::uuid IS NULL OR e.source_id=%s::uuid)
                                AND (NOT %s OR EXISTS (SELECT 1 FROM subject_representative_face preview
                                     WHERE preview.example_id=e.example_id AND preview.active))
                              ORDER BY e.example_id LIMIT %s""",
                (sid, after, after, source_id, source_id, with_previews, limit + 1),
            )
            rows = cursor.fetchall()
            examples = []
            for r in rows[:limit]:
                examples.append(
                    {
                        "example_id": str(r[0]),
                        "source_id": str(r[1]),
                        "model_version": r[2],
                        "start_ms": r[3],
                        "end_ms": r[4],
                        "quality_score": r[5],
                        "processing_job_id": None if r[6] is None else str(r[6]),
                        "run_id": None if r[7] is None else str(r[7]),
                        "content_type": r[8],
                        "page_url": r[9],
                        "preview_url": f"/api/gallery/faces/{r[10]}" if r[10] else None,
                        "face_track_id": None if r[11] is None else str(r[11]),
                        "submission_group_id": None if r[12] is None else str(r[12]),
                    }
                )
            return {
                "subject_id": sid,
                "examples": examples,
                "source_time_range": source_time_range,
                "next_cursor": str(rows[limit - 1][0]) if len(rows) > limit else None,
            }

    @staticmethod
    def _version(actual, expected):
        if type(expected) is not int or expected != actual:
            raise SubjectError(
                409,
                "stale_subject",
                "This subject changed. Refresh its details and review the action again.",
            )

    def _operation(self, cursor, sid, action, data, actor):
        operation = subject_uuid(data.get("operation_id"))
        fingerprint = hashlib.sha256(
            json.dumps(
                {"subject_id": sid, "action": action, "data": data}, sort_keys=True
            ).encode()
        ).hexdigest()
        cursor.execute(
            "SELECT actor,request_fingerprint,result FROM subject_change_event WHERE operation_id=%s",
            (operation,),
        )
        row = cursor.fetchone()
        if row:
            if row[0] != actor or row[1] != fingerprint:
                raise SubjectError(
                    409,
                    "operation_conflict",
                    "This operation ID was already used for a different request.",
                )
            return operation, fingerprint, row[2]
        return operation, fingerprint, None

    @staticmethod
    def _record(cursor, operation, actor, action, fingerprint, before, result):
        cursor.execute(
            "INSERT INTO subject_change_event(operation_id,actor,action,request_fingerprint,details,result) VALUES (%s,%s,%s,%s,%s::jsonb,%s::jsonb)",
            (
                operation,
                actor,
                action,
                fingerprint,
                json.dumps(before, default=str),
                json.dumps(result, default=str),
            ),
        )

    def edit(self, subject_id, data, actor):
        sid = subject_uuid(subject_id)
        if set(data) != {
            "operation_id",
            "version",
            "identity_version",
            "display_name",
            "external_identity_ref",
        }:
            raise SubjectError(
                422,
                "invalid_details",
                "Provide the display name, external reference, versions, and operation ID.",
            )
        fields = []
        for key in ("display_name", "external_identity_ref"):
            value = data[key]
            if value is not None and (not isinstance(value, str) or len(value) > 200):
                raise SubjectError(
                    422,
                    "invalid_details",
                    "Names and external references must be text up to 200 characters.",
                )
            fields.append((value.strip() or None) if value else None)
        with self.transaction(write=True) as cursor:
            operation, fingerprint, replay = self._operation(
                cursor, sid, "edit", data, actor
            )
            if replay is not None:
                return replay
            before = self._detail(cursor, sid)
            if before["subject_id"] != sid:
                raise SubjectError(
                    409,
                    "subject_merged",
                    "This subject was combined. Review the surviving subject before editing.",
                )
            self._version(before["version"], data["version"])
            if before["identity_version"] != data["identity_version"] or (
                data["identity_version"] is not None
                and type(data["identity_version"]) is not int
            ):
                raise SubjectError(
                    409,
                    "stale_identity",
                    "These identity details changed. Refresh and review them again.",
                )
            identity_id = before["identity_id"]
            if identity_id:
                cursor.execute(
                    "UPDATE identity SET display_name=%s,external_identity_ref=%s,row_version=row_version+1,updated_at=now() WHERE identity_id=%s",
                    (*fields, identity_id),
                )
                cursor.execute(
                    "UPDATE subject SET row_version=row_version+1,updated_at=now() WHERE identity_id=%s",
                    (identity_id,),
                )
            elif any(fields):
                cursor.execute(
                    "INSERT INTO identity(display_name,external_identity_ref) VALUES (%s,%s) RETURNING identity_id",
                    fields,
                )
                identity_id = cursor.fetchone()[0]
                cursor.execute(
                    "UPDATE subject SET identity_id=%s,row_version=row_version+1,updated_at=now() WHERE subject_id=%s",
                    (identity_id, sid),
                )
            result = self._detail(cursor, sid)
            self._record(cursor, operation, actor, "edit", fingerprint, before, result)
            return result

    def _correct(self, subject_id, data, actor, action):
        sid = subject_uuid(subject_id)
        with self.transaction(write=True) as cursor:
            operation, fingerprint, replay = self._operation(
                cursor, sid, action, data, actor
            )
            if replay is not None:
                return replay
            before = self._detail(cursor, sid)
            if before["subject_id"] != sid:
                raise SubjectError(
                    409,
                    "subject_merged",
                    "Review the surviving subject before applying this correction.",
                )
            self._version(before["version"], data.get("version"))
            target_id = (
                subject_uuid(data.get("other_subject_id"))
                if action == "combine"
                else (
                    subject_uuid(data["target_subject_id"])
                    if data.get("target_subject_id")
                    else None
                )
            )
            if target_id == sid:
                raise SubjectError(422, "same_subject", "Choose a different subject.")
            target = self._detail(cursor, target_id) if target_id else None
            if target and target["subject_id"] != target_id:
                raise SubjectError(
                    409,
                    "subject_merged",
                    "The destination changed. Select its surviving subject and review again.",
                )
            if target:
                self._version(target["version"], data.get("target_version"))
                if before["model_version"] != target["model_version"]:
                    raise SubjectError(
                        409,
                        "model_mismatch",
                        "Subjects must use the same recognition model.",
                    )
            elif action == "move":
                if data.get("target_version") is not None:
                    raise SubjectError(
                        422, "invalid_target", "A new subject has no version."
                    )
                target_id = str(uuid.uuid5(uuid.UUID(operation), "moved-subject"))
                cursor.execute(
                    "INSERT INTO subject(subject_id,model_version) VALUES (%s,%s)",
                    (target_id, before["model_version"]),
                )
            ids = sorted([sid, target_id])
            cursor.execute(
                "SELECT subject_id FROM subject WHERE subject_id=ANY(%s::uuid[]) ORDER BY subject_id FOR UPDATE",
                (ids,),
            )
            if action == "combine":
                source, destination = target_id, sid
                cursor.execute(
                    "SELECT example_id FROM subject_example WHERE subject_id=%s ORDER BY example_id",
                    (source,),
                )
                example_ids = [str(r[0]) for r in cursor.fetchall()]
            else:
                source, destination = sid, target_id
                incoming = data.get("example_ids")
                if (
                    not isinstance(incoming, list)
                    or not incoming
                    or len(incoming) > 1000
                ):
                    raise SubjectError(
                        422, "invalid_examples", "Choose between 1 and 1000 examples."
                    )
                example_ids = [subject_uuid(x) for x in incoming]
                if len(example_ids) != len(set(example_ids)):
                    raise SubjectError(
                        422, "invalid_examples", "Choose each example once."
                    )
                cursor.execute(
                    "SELECT example_id FROM subject_example WHERE subject_id=%s AND example_id=ANY(%s::uuid[]) FOR UPDATE",
                    (source, example_ids),
                )
                if {str(r[0]) for r in cursor.fetchall()} != set(example_ids):
                    raise SubjectError(
                        409,
                        "examples_changed",
                        "Some examples are no longer assigned to this subject. Refresh and review.",
                    )
            cursor.execute(
                "UPDATE subject_example SET subject_id=%s WHERE subject_id=%s AND example_id=ANY(%s::uuid[])",
                (destination, source, example_ids),
            )
            if action == "combine":
                cursor.execute(
                    "UPDATE subject SET merged_into_subject_id=%s WHERE subject_id=%s",
                    (destination, source),
                )
            recalculate_subjects(cursor, ids)
            result = {
                "subject": self._detail(cursor, sid),
                "destination": self._detail(cursor, destination),
                "moved_example_ids": example_ids,
            }
            self._record(
                cursor,
                operation,
                actor,
                action,
                fingerprint,
                {
                    "subject": before,
                    "other": target,
                    "example_ids": example_ids,
                    "from_subject_id": source,
                    "to_subject_id": destination,
                },
                result,
            )
            return result

    def bulk_combine(self, subject_id, data, actor):
        with self.transaction(write=True) as cursor:
            return self._bulk_combine(cursor, subject_id, data, actor)

    def _bulk_combine(self, cursor, subject_id, data, actor, *, scope=None, validate=None):
        if set(data) != {'operation_id', 'version', 'subjects'}:
            raise SubjectError(422, 'invalid_combine', 'Provide the destination version and selected subjects.')
        sid = subject_uuid(subject_id)
        members = data['subjects']
        if not isinstance(members, list) or not 1 <= len(members) <= 49:
            raise SubjectError(422, 'invalid_combine', 'Choose between 2 and 50 subjects in total.')
        versions = {}
        for member in members:
            if not isinstance(member, dict) or set(member) != {'subject_id', 'version'}:
                raise SubjectError(422, 'invalid_combine', 'Each selected subject needs its ID and version.')
            member_id = subject_uuid(member['subject_id'])
            if member_id == sid or member_id in versions:
                raise SubjectError(422, 'invalid_combine', 'Choose each subject once.')
            versions[member_id] = member['version']
        operation, fingerprint, replay = self._operation(cursor, sid, 'combine', {**data, **({'source_merge': scope} if scope else {})}, actor)
        if replay is not None:
            return replay
        if validate is not None:
            validate(cursor)
        destination = self._detail(cursor, sid)
        if destination['subject_id'] != sid:
            raise SubjectError(409, 'subject_merged', 'Refresh the destination subject.')
        self._version(destination['version'], data['version'])
        groups = []
        # Validate every member before changing any membership.
        for member_id in sorted(versions):
            member = self._detail(cursor, member_id)
            if member['subject_id'] != member_id:
                raise SubjectError(409, 'subject_merged', 'A selected subject was already merged. Refresh the selection.')
            self._version(member['version'], versions[member_id])
            if member['model_version'] != destination['model_version']:
                raise SubjectError(409, 'model_mismatch', 'Subjects must use the same recognition model.')
            cursor.execute('SELECT example_id FROM subject_example WHERE subject_id=%s ORDER BY example_id', (member_id,))
            groups.append({'subject': member, 'example_ids': [str(r[0]) for r in cursor.fetchall()]})
        for group in groups:
            member_id = group['subject']['subject_id']
            cursor.execute('UPDATE subject_example SET subject_id=%s WHERE subject_id=%s', (sid, member_id))
            cursor.execute('UPDATE subject SET merged_into_subject_id=%s WHERE subject_id=%s', (sid, member_id))
        recalculate_subjects(cursor, [sid, *versions])
        result = {'destination': self._detail(cursor, sid), 'operation_id': operation,
                  'merged_subject_ids': sorted(versions)}
        self._record(cursor, operation, actor, 'combine', fingerprint,
                     {'subject': destination, 'to_subject_id': sid, 'members': groups, **({'source_merge': scope} if scope else {})}, result)
        return result

    @staticmethod
    def _merge_members(details):
        if 'members' in details:
            return details['members']
        # Existing pairwise merges already recorded enough information to separate.
        if details.get('other') and details.get('example_ids'):
            return [{'subject': details['other'], 'example_ids': details['example_ids']}]
        return []

    def _can_separate(self, cursor, sid, member):
        old_id = member['subject']['subject_id']
        ids = member['example_ids']
        cursor.execute('SELECT merged_into_subject_id FROM subject WHERE subject_id=%s', (old_id,))
        row = cursor.fetchone()
        if not row or str(row[0]) != sid or not ids:
            return False
        cursor.execute('SELECT count(*) FROM subject_example WHERE subject_id=%s AND example_id=ANY(%s::uuid[])', (sid, ids))
        if cursor.fetchone()[0] != len(ids):
            return False
        cursor.execute('SELECT count(*) FROM subject_example WHERE subject_id=%s', (old_id,))
        return cursor.fetchone()[0] == 0

    def merge_members(self, subject_id):
        with self.transaction() as cursor:
            sid = resolve_subject(cursor, subject_uuid(subject_id))
            detail = self._detail(cursor, sid)
            cursor.execute("""SELECT operation_id,details,created_at FROM subject_change_event
                WHERE action='combine' AND details->>'to_subject_id'=%s
                ORDER BY created_at DESC,operation_id DESC LIMIT 20""", (sid,))
            events = cursor.fetchall()
            members = []
            for operation, details, created in events:
                for member in self._merge_members(details):
                    members.append({'operation_id': str(operation),
                        'subject_id': member['subject']['subject_id'],
                        'display_name': member['subject'].get('display_name'),
                        'example_count': len(member['example_ids']),
                        'can_separate': self._can_separate(cursor, sid, member),
                        'created_at': created.isoformat()})
            return {'subject_id': sid, 'version': detail['version'], 'members': members}

    def separate_merge(self, subject_id, data, actor):
        if set(data) != {'operation_id', 'version', 'merge_operation_id', 'member_subject_id'}:
            raise SubjectError(422, 'invalid_separation', 'Choose a previous merge member and the current subject version.')
        sid = subject_uuid(subject_id)
        merge_id = subject_uuid(data['merge_operation_id'])
        member_id = subject_uuid(data['member_subject_id'])
        with self.transaction(write=True) as cursor:
            operation, fingerprint, replay = self._operation(cursor, sid, 'move', data, actor)
            if replay is not None:
                return replay
            before = self._detail(cursor, sid)
            if before['subject_id'] != sid:
                raise SubjectError(409, 'subject_merged', 'Refresh the surviving subject before separating.')
            self._version(before['version'], data['version'])
            cursor.execute("SELECT details FROM subject_change_event WHERE operation_id=%s AND action='combine'", (merge_id,))
            row = cursor.fetchone()
            if not row or row[0].get('to_subject_id') != sid:
                raise SubjectError(409, 'merge_changed', 'This merge is not available on this subject.')
            member = next((m for m in self._merge_members(row[0]) if m['subject']['subject_id'] == member_id), None)
            if member is None or not self._can_separate(cursor, sid, member):
                raise SubjectError(409, 'examples_changed', 'These examples have changed. Select the examples to separate manually.')
            cursor.execute('UPDATE subject SET merged_into_subject_id=NULL WHERE subject_id=%s', (member_id,))
            cursor.execute('UPDATE subject_example SET subject_id=%s WHERE subject_id=%s AND example_id=ANY(%s::uuid[])',
                           (member_id, sid, member['example_ids']))
            recalculate_subjects(cursor, [sid, member_id])
            result = {'subject': self._detail(cursor, sid), 'destination': self._detail(cursor, member_id),
                      'moved_example_ids': member['example_ids']}
            self._record(cursor, operation, actor, 'move', fingerprint,
                         {'subject': before, 'example_ids': member['example_ids'], 'from_subject_id': sid,
                          'to_subject_id': member_id, 'separated_merge_operation_id': merge_id}, result)
            return result

    def combine(self, subject_id, data, actor):
        if set(data) != {
            "operation_id",
            "version",
            "other_subject_id",
            "target_version",
        }:
            raise SubjectError(
                422,
                "invalid_combine",
                "Provide both subjects' versions, the other subject ID, and operation ID.",
            )
        return self._correct(subject_id, data, actor, "combine")

    def move(self, subject_id, data, actor):
        if set(data) != {
            "operation_id",
            "version",
            "example_ids",
            "target_subject_id",
            "target_version",
        }:
            raise SubjectError(
                422,
                "invalid_move",
                "Provide examples, destination, versions, and operation ID.",
            )
        return self._correct(subject_id, data, actor, "move")

    def coverage(self):
        with self.transaction() as cursor:
            cursor.execute("""SELECT
                (SELECT count(*) FROM submission_face_group g WHERE g.enrolled_at IS NOT NULL AND NOT EXISTS (SELECT 1 FROM subject_example e WHERE e.submission_group_id=g.group_id)),
                (SELECT count(*) FROM subject_example e JOIN subject s USING(subject_id)
                 WHERE e.model_version<>s.model_version),
                (SELECT count(*) FROM subject_example),
                (SELECT count(*) FROM subject s WHERE merged_into_subject_id IS NULL AND sample_count<>(SELECT count(*) FROM subject_example e WHERE e.subject_id=s.subject_id))""")
            report = dict(
                zip(
                    (
                        "missing_enrollments",
                        "model_mismatches",
                        "examples",
                        "count_mismatches",
                    ),
                    cursor.fetchone(),
                )
            )
            cursor.execute("""SELECT
                (SELECT count(*) FROM subject_example WHERE face_track_id IS NOT NULL),
                (SELECT count(*) FROM submission_face_group WHERE enrolled_at IS NOT NULL),
                (SELECT count(DISTINCT e.subject_id) FROM subject_example e JOIN source_asset sa USING(source_id)
                 WHERE sa.deleted_at IS NULL AND sa.storage_kind IN ('managed','archive')),
                (SELECT count(DISTINCT e.subject_id) FROM subject_representative_face r JOIN subject_example e USING(example_id) WHERE r.active),
                (SELECT count(*) FROM subject_representative_face WHERE active),
                (SELECT count(*) FROM gallery_fallback_source)""")
            report.update(
                zip(
                    (
                        "enrolled_tracks",
                        "eligible_enrollments",
                        "subjects_with_retained_sources",
                        "subjects_with_gallery",
                        "active_gallery_images",
                        "gallery_fallback_sources",
                    ),
                    cursor.fetchone(),
                    strict=True,
                )
            )
            return report


def save_assignments(cursor, run_id, group_ids, assignments):
    if not isinstance(assignments, list) or len(assignments) > len(group_ids):
        raise SubjectError(
            422, "invalid_assignments", "Provide a list of enrollment groups."
        )
    cursor.execute("SELECT handling_policy FROM media_run WHERE run_id=%s", (run_id,))
    policy = cursor.fetchone()[0]
    if assignments and policy == "search_then_discard":
        raise SubjectError(
            422, "enrollment_required", "Assignments require an enrollment policy."
        )
    selected, assigned, assignment_ids = set(group_ids), set(), set()
    for assignment in assignments:
        if not isinstance(assignment, dict) or set(assignment) != {
            "assignment_id",
            "group_ids",
        }:
            raise SubjectError(
                422,
                "invalid_assignment",
                "Each group needs only an assignment ID and member IDs.",
            )
        aid = subject_uuid(assignment["assignment_id"])
        members = assignment["group_ids"]
        if not isinstance(members, list) or not members:
            raise SubjectError(
                422,
                "invalid_assignment",
                "Each enrollment group needs at least one face track.",
            )
        members = [subject_uuid(x) for x in members]
        if (
            len(members) != len(set(members))
            or assigned.intersection(members)
            or not set(members).issubset(selected)
            or aid in assignment_ids
        ):
            raise SubjectError(
                422,
                "conflicting_assignment",
                "Each selected face track can belong to one enrollment group.",
            )
        assigned.update(members)
        assignment_ids.add(aid)
        cursor.execute(
            "SELECT DISTINCT embedding_model_version FROM submission_face_group WHERE run_id=%s AND group_id=ANY(%s::uuid[])",
            (run_id, members),
        )
        versions = {row[0] for row in cursor.fetchall()}
        if len(versions) != 1:
            raise SubjectError(
                409,
                "model_mismatch",
                "Grouped examples must use the same recognition model.",
            )
        cursor.execute(
            "SELECT run_id FROM enrollment_assignment WHERE assignment_id=%s", (aid,)
        )
        existing = cursor.fetchone()
        if existing:
            raise SubjectError(
                409,
                "assignment_id_used",
                "This assignment ID was already submitted. Refresh the run.",
            )
        cursor.execute(
            "INSERT INTO enrollment_assignment(assignment_id,run_id) VALUES (%s,%s)",
            (aid, run_id),
        )
        for member in members:
            cursor.execute(
                "INSERT INTO enrollment_assignment_member(group_id,assignment_id) VALUES (%s,%s)",
                (member, aid),
            )

    if policy != "search_then_discard":
        for member in sorted(selected - assigned):
            aid = str(uuid.uuid5(uuid.UUID(run_id), "singleton:" + member))
            cursor.execute(
                "INSERT INTO enrollment_assignment(assignment_id,run_id) VALUES (%s,%s)",
                (aid, run_id),
            )
            cursor.execute(
                "INSERT INTO enrollment_assignment_member(group_id,assignment_id) VALUES (%s,%s)",
                (member, aid),
            )


def enrollment_targets(cursor, run_id, model_version):
    cursor.execute(
        """SELECT a.assignment_id,array_agg(m.group_id)
           FROM enrollment_assignment a JOIN enrollment_assignment_member m USING(assignment_id)
           WHERE a.run_id=%s GROUP BY a.assignment_id ORDER BY a.assignment_id""",
        (run_id,),
    )
    assignments = cursor.fetchall()
    targets = {}
    for aid, members in assignments:
        sid = str(uuid.uuid5(uuid.UUID(str(aid)), "enrollment-subject"))
        cursor.execute(
            "INSERT INTO subject(subject_id,model_version) VALUES (%s,%s) ON CONFLICT (subject_id) DO NOTHING",
            (sid, model_version),
        )
        for gid in members:
            targets[str(gid)] = sid
    return targets
