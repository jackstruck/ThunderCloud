#!/usr/bin/env python3
"""Create an explicit, reviewed selection of manifest items not yet successful."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from worker.config import Settings
from worker.db import Database
from worker.manifest import read_manifest


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--manifest", type=Path, required=True)
    result.add_argument("--output", type=Path, required=True)
    result.add_argument("--confirm-count", type=int, required=True)
    return result


def main(argv=None) -> None:
    args = parser().parse_args(argv)
    settings = Settings.from_env()
    settings.validate()
    database = Database(
        settings.cloud_sql_instance,
        settings.db_user,
        settings.db_name,
        settings.cloud_sql_ip_type,
    )
    connection = database.connect()
    cursor = connection.cursor()
    try:
        cursor.execute(
            """SELECT DISTINCT external_source_ref FROM source_asset s
               JOIN processing_job j USING(source_id) WHERE j.status='succeeded'"""
        )
        completed = {row[0] for row in cursor.fetchall()}
    finally:
        cursor.close()
        connection.close()
        database.close()

    remaining = []
    seen = set()
    for item in read_manifest(args.manifest, settings.bucket, settings.source_prefix):
        identity = item.sha256 or item.object_uri
        if item.object_uri in completed or identity in seen:
            continue
        seen.add(identity)
        remaining.append(item.uid)
    if len(remaining) != args.confirm_count:
        raise RuntimeError(
            f"refusing to write selection: expected {args.confirm_count}, found {len(remaining)}"
        )
    descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        output.write("\n".join(remaining) + "\n")
    print(f"wrote {len(remaining)} unique unprocessed manifest UIDs")


if __name__ == "__main__":
    main()
