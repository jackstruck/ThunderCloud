from __future__ import annotations

import argparse
import uuid

import numpy as np

from .config import Settings
from .db import Database
from .subject_management import SubjectManagement


def _unit(values) -> np.ndarray:
    vector = np.asarray(values, dtype=np.float32)
    norm = np.linalg.norm(vector)
    if norm == 0:
        raise ValueError("cannot normalize a zero embedding")
    return vector / norm


class GalleryCorrection:
    """CLI adapter for the same transactions used by the subject-management UI."""

    def __init__(self, database, actor="operator-cli"):
        self.database = database
        self.actor = actor
        self.subjects = SubjectManagement(database)

    def merge(self, keep_id: str, merge_id: str) -> None:
        keep = self.subjects.subject(keep_id)
        other = self.subjects.subject(merge_id)
        return self.subjects.combine(
            keep["subject_id"],
            {
                "operation_id": str(uuid.uuid4()),
                "version": keep["version"],
                "other_subject_id": other["subject_id"],
                "target_version": other["version"],
            },
            self.actor,
        )

    def split_group(self, group_id: str) -> str:
        with self.subjects.transaction() as cursor:
            cursor.execute(
                "SELECT subject_id,example_id FROM subject_example WHERE submission_group_id=%s",
                (group_id,),
            )
            row = cursor.fetchone()
            if not row:
                raise ValueError(
                    "enrolled example not found; complete example backfill first"
                )
            sid, example_id = map(str, row)
        subject = self.subjects.subject(sid)
        result = self.subjects.move(
            subject["subject_id"],
            {
                "operation_id": str(uuid.uuid4()),
                "version": subject["version"],
                "example_ids": [example_id],
                "target_subject_id": None,
                "target_version": None,
            },
            self.actor,
        )
        return result["destination"]["subject_id"]


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
    database = Database(
        settings.cloud_sql_instance,
        settings.db_user,
        settings.db_name,
        settings.cloud_sql_ip_type,
    )
    try:
        correction = GalleryCorrection(database, actor=settings.db_user)
        if args.command == "merge":
            correction.merge(args.keep_subject_id, args.merge_subject_id)
        else:
            print(correction.split_group(args.group_id))
    finally:
        database.close()
