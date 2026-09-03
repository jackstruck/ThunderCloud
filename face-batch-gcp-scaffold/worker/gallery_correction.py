from __future__ import annotations

import argparse
import json

import numpy as np

from .config import Settings
from .db import Database, pgvector


def _unit(values) -> np.ndarray:
    vector = np.asarray(values, dtype=np.float32)
    norm = np.linalg.norm(vector)
    if norm == 0:
        raise ValueError("cannot normalize a zero embedding")
    return vector / norm


class GalleryCorrection:
    """Small operator command for the Phase 2 merge/split repair path."""

    def __init__(self, database):
        self.database = database

    def merge(self, keep_id: str, merge_id: str) -> None:
        if keep_id == merge_id:
            raise ValueError("subjects must differ")
        connection = self.database.connect()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """SELECT subject_id, canonical_embedding::text, sample_count, model_version
                   FROM subject WHERE subject_id=ANY(%s::uuid[])
                   ORDER BY subject_id FOR UPDATE""",
                ([keep_id, merge_id],),
            )
            rows = {str(row[0]): row for row in cursor.fetchall()}
            if set(rows) != {keep_id, merge_id}:
                raise ValueError("subject not found")
            keep, merged = rows[keep_id], rows[merge_id]
            if keep[3] != merged[3]:
                raise ValueError("cannot merge different embedding model versions")
            count = int(keep[2]) + int(merged[2])
            vector = _unit(
                np.asarray(json.loads(keep[1])) * int(keep[2])
                + np.asarray(json.loads(merged[1])) * int(merged[2])
            )
            cursor.execute(
                """UPDATE subject SET canonical_embedding=%s::vector, sample_count=%s,
                          updated_at=now() WHERE subject_id=%s""",
                (pgvector(vector), count, keep_id),
            )
            cursor.execute("UPDATE face_track SET subject_id=%s WHERE subject_id=%s", (keep_id, merge_id))
            cursor.execute("UPDATE submission_enrollment SET subject_id=%s WHERE subject_id=%s", (keep_id, merge_id))
            cursor.execute(
                """UPDATE subject SET canonical_embedding=NULL, sample_count=0,
                          metadata=metadata || jsonb_build_object('merged_into',%s),
                          updated_at=now() WHERE subject_id=%s""",
                (keep_id, merge_id),
            )
            cursor.execute(
                """UPDATE subject_representative_face SET active=false, retired_at=now()
                   WHERE subject_id=ANY(%s::uuid[]) AND active""",
                ([keep_id, merge_id],),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def split_group(self, group_id: str) -> str:
        connection = self.database.connect()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """SELECT e.subject_id, g.aggregate_embedding::text, s.canonical_embedding::text,
                          s.sample_count, s.model_version
                   FROM submission_enrollment e JOIN submission_face_group g USING(group_id)
                   JOIN subject s USING(subject_id) WHERE e.group_id=%s FOR UPDATE OF s""",
                (group_id,),
            )
            row = cursor.fetchone()
            if not row:
                raise ValueError("enrolled group not found")
            subject_id, group_text, old_text, count, model = row
            if int(count) <= 1:
                raise ValueError("cannot split the subject's only sample")
            group_vector = _unit(json.loads(group_text))
            remainder = _unit(
                np.asarray(json.loads(old_text)) * int(count) - group_vector
            )
            cursor.execute(
                """INSERT INTO subject (canonical_embedding,model_version,sample_count)
                   VALUES (%s::vector,%s,1) RETURNING subject_id""",
                (pgvector(group_vector), model),
            )
            new_id = str(cursor.fetchone()[0])
            cursor.execute("UPDATE submission_enrollment SET subject_id=%s WHERE group_id=%s", (new_id, group_id))
            cursor.execute(
                """UPDATE subject SET canonical_embedding=%s::vector,
                          sample_count=sample_count-1, updated_at=now() WHERE subject_id=%s""",
                (pgvector(remainder), subject_id),
            )
            cursor.execute(
                """UPDATE subject_representative_face SET active=false, retired_at=now()
                   WHERE subject_id=%s AND active""",
                (subject_id,),
            )
            connection.commit()
            return new_id
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Repair Phase 2 subject clustering")
    sub = parser.add_subparsers(dest="command", required=True)
    merge = sub.add_parser("merge")
    merge.add_argument("keep_subject_id")
    merge.add_argument("merge_subject_id")
    split = sub.add_parser("split-group")
    split.add_argument("group_id")
    args = parser.parse_args(argv)
    settings = Settings.from_env()
    database = Database(settings.cloud_sql_instance, settings.db_user, settings.db_name,
                        settings.cloud_sql_ip_type)
    try:
        correction = GalleryCorrection(database)
        if args.command == "merge":
            correction.merge(args.keep_subject_id, args.merge_subject_id)
        else:
            print(correction.split_group(args.group_id))
    finally:
        database.close()
