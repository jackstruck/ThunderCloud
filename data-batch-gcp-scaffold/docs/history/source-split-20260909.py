"""Audited, resumable source-based partitioning; never compares face representations."""

from __future__ import annotations

import argparse
import hashlib
import json
import uuid
from pathlib import Path

from worker.config import Settings
from worker.db import Database
from worker.subject_management import (
    SubjectError,
    SubjectManagement,
    recalculate_subjects,
    reconcile_gallery,
)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def make_plan(service):
    batch_id = str(uuid.uuid4())
    with service.transaction() as c:
        c.execute("""SELECT s.subject_id,s.row_version,s.model_version,s.identity_id
                     FROM subject s WHERE s.merged_into_subject_id IS NULL
                     AND (SELECT count(DISTINCT source_id) FROM subject_example e WHERE e.subject_id=s.subject_id)>1
                     ORDER BY s.subject_id""")
        subjects = {
            str(r[0]): {
                "subject_id": str(r[0]),
                "version": r[1],
                "model_version": r[2],
                "identity_id": str(r[3]) if r[3] else None,
                "partitions": [],
            }
            for r in c.fetchall()
        }
        c.execute(
            """SELECT subject_id,source_id,array_agg(example_id ORDER BY example_id)
                     FROM subject_example WHERE subject_id=ANY(%s::uuid[])
                     GROUP BY subject_id,source_id ORDER BY subject_id,count(*) DESC,source_id""",
            (list(subjects),),
        )
        for sid, source, examples in c.fetchall():
            item = subjects[str(sid)]
            op = str(uuid.uuid5(uuid.UUID(batch_id), str(sid)))
            destination = (
                str(sid)
                if not item["partitions"]
                else str(uuid.uuid5(uuid.UUID(op), str(source)))
            )
            item["operation_id"] = op
            item["partitions"].append(
                {
                    "source_id": str(source),
                    "subject_id": destination,
                    "example_ids": list(map(str, examples)),
                }
            )
    return {"batch_id": batch_id, "subjects": list(subjects.values())}


def apply_batch(service, items, batch_id, actor):
    with service.transaction(write=True) as c:
        c.execute(
            "SELECT operation_id,actor,request_fingerprint FROM subject_change_event WHERE operation_id=ANY(%s::uuid[])",
            ([item["operation_id"] for item in items],),
        )
        replays = {str(row[0]): (row[1], row[2]) for row in c.fetchall()}
        pending = []
        for item in items:
            replay = replays.get(item["operation_id"])
            if replay:
                if replay != (actor, fingerprint(item)):
                    raise ValueError(
                        "Operation replay does not match this plan and actor"
                    )
            else:
                pending.append(item)
        if not pending:
            return 0
        ids = [x["subject_id"] for x in pending]
        c.execute(
            "SELECT subject_id,row_version,merged_into_subject_id,identity_id,model_version FROM subject WHERE subject_id=ANY(%s::uuid[]) ORDER BY subject_id FOR UPDATE",
            (ids,),
        )
        rows = {str(r[0]): r for r in c.fetchall()}
        c.execute(
            "SELECT example_id,subject_id,source_id FROM subject_example WHERE subject_id=ANY(%s::uuid[]) ORDER BY example_id FOR UPDATE",
            (ids,),
        )
        actual = {(str(e), str(s), str(source)) for e, s, source in c.fetchall()}
        expected = {
            (e, item["subject_id"], part["source_id"])
            for item in pending
            for part in item["partitions"]
            for e in part["example_ids"]
        }
        if actual != expected:
            raise SubjectError(
                409,
                "examples_changed",
                "Examples changed since the source split was planned",
            )
        new_ids, models, example_ids, owners, old_ids, sources, destinations = (
            [],
            [],
            [],
            [],
            [],
            [],
            [],
        )
        for item in pending:
            row = rows.get(item["subject_id"])
            if (
                not row
                or row[1] != item["version"]
                or row[2] is not None
                or (str(row[3]) if row[3] else None) != item["identity_id"]
                or row[4] != item["model_version"]
            ):
                raise SubjectError(
                    409,
                    "stale_subject",
                    "Subject changed since the source split was planned",
                )
            for part in item["partitions"]:
                destination = part["subject_id"]
                old_ids.append(item["subject_id"])
                sources.append(part["source_id"])
                destinations.append(destination)
                if destination != item["subject_id"]:
                    new_ids.append(destination)
                    models.append(item["model_version"])
                    example_ids.extend(part["example_ids"])
                    owners.extend([destination] * len(part["example_ids"]))
        c.execute(
            """INSERT INTO subject(subject_id,model_version)
                     SELECT id,model FROM unnest(%s::uuid[],%s::text[]) AS data(id,model)""",
            (new_ids, models),
        )
        c.execute(
            """UPDATE subject_example e SET subject_id=data.owner FROM unnest(%s::uuid[],%s::uuid[]) AS data(id,owner)
                     WHERE e.example_id=data.id""",
            (example_ids, owners),
        )
        c.execute(
            """UPDATE face_track ft SET subject_id=e.subject_id FROM subject_example e
                     WHERE e.face_track_id=ft.track_id AND e.example_id=ANY(%s::uuid[])""",
            (example_ids,),
        )
        c.execute(
            """UPDATE submission_enrollment se SET subject_id=e.subject_id FROM subject_example e
                     WHERE e.submission_group_id=se.group_id AND e.example_id=ANY(%s::uuid[])""",
            (example_ids,),
        )
        c.execute(
            """UPDATE subject_representative_face r SET subject_id=data.owner
                     FROM unnest(%s::uuid[],%s::uuid[],%s::uuid[]) AS data(original,source,owner)
                     WHERE r.subject_id=data.original AND r.source_id=data.source""",
            (old_ids, sources, destinations),
        )
        recalculate_subjects(c, ids + new_ids)
        reconcile_gallery(c, ids + new_ids)
        operations, fingerprints, details, results = [], [], [], []
        for item in pending:
            operations.append(item["operation_id"])
            fingerprints.append(fingerprint(item))
            details.append(
                json.dumps(
                    {
                        "kind": "split_by_source",
                        "batch_id": batch_id,
                        "before_and_partitions": item,
                    }
                )
            )
            results.append(
                json.dumps(
                    {
                        "original_subject_id": item["subject_id"],
                        "subjects": [
                            {
                                "subject_id": p["subject_id"],
                                "source_id": p["source_id"],
                                "example_count": len(p["example_ids"]),
                            }
                            for p in item["partitions"]
                        ],
                    }
                )
            )
        c.execute(
            """INSERT INTO subject_change_event(operation_id,actor,action,request_fingerprint,details,result)
                     SELECT id,%s,'move',fingerprint,details::jsonb,result::jsonb
                     FROM unnest(%s::uuid[],%s::text[],%s::text[],%s::text[]) AS data(id,fingerprint,details,result)""",
            (actor, operations, fingerprints, details, results),
        )
        return len(pending)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--actor", required=True)
    parser.add_argument("--batch-size", type=int, default=20)
    args = parser.parse_args()
    if not 1 <= args.batch_size <= 100:
        parser.error("batch-size must be between 1 and 100")
    settings = Settings.from_env()
    db = Database(
        settings.cloud_sql_instance,
        settings.db_user,
        settings.db_name,
        settings.cloud_sql_ip_type,
    )
    service = SubjectManagement(db)
    try:
        if args.apply:
            plan = json.loads(args.plan.read_text())
            for offset in range(0, len(plan["subjects"]), args.batch_size):
                count = apply_batch(
                    service,
                    plan["subjects"][offset : offset + args.batch_size],
                    plan["batch_id"],
                    args.actor,
                )
                print(
                    json.dumps(
                        {
                            "processed": min(
                                offset + args.batch_size, len(plan["subjects"])
                            ),
                            "total": len(plan["subjects"]),
                            "changed": count,
                        }
                    ),
                    flush=True,
                )
        else:
            plan = make_plan(service)
            with args.plan.open("x") as f:
                args.plan.chmod(0o600)
                json.dump(plan, f, indent=2)
            print(
                json.dumps(
                    {
                        "subjects": len(plan["subjects"]),
                        "new_subjects": sum(
                            len(i["partitions"]) - 1 for i in plan["subjects"]
                        ),
                        "examples": sum(
                            len(p["example_ids"])
                            for i in plan["subjects"]
                            for p in i["partitions"]
                        ),
                    }
                ),
                flush=True,
            )
    finally:
        db.close()


if __name__ == "__main__":
    main()
