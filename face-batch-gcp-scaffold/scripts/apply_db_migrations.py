#!/usr/bin/env python3
"""Apply ordered, tracked schema migrations and shared least-privilege grants."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from google.cloud import secretmanager
from google.cloud.sql.connector import Connector, IPTypes

# Support both `python scripts/apply_db_migrations.py` and module invocation.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from maintenance.migrations import apply_migrations, ordered_migrations


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--project", default="teak-banner-dome")
    result.add_argument(
        "--instance", default="teak-banner-dome:us-central1:face-batch-pg"
    )
    result.add_argument("--database", default="face_index")
    result.add_argument("--admin-user", default="postgres")
    result.add_argument("--app-user", action="append", required=True)
    result.add_argument("--source-bucket", help="Reviewed bucket for existing retained sources")
    result.add_argument("--ip-type", choices=("PUBLIC", "PRIVATE"), default="PUBLIC")
    result.add_argument(
        "--password-secret", default="face-batch-postgres-admin-password"
    )
    result.add_argument(
        "--schema-reference",
        type=Path,
        help="Reference produced by isolated maintenance validation",
    )
    result.add_argument(
        "--adopt-through",
        type=int,
        help="Explicit installed version for an untracked database; requires reference equality",
    )
    return result


def main(argv=None) -> None:
    args = parser().parse_args(argv)
    password_client = secretmanager.SecretManagerServiceClient()
    secret = (
        password_client.access_secret_version(
            request={
                "name": f"projects/{args.project}/secrets/{args.password_secret}/versions/latest"
            }
        )
        .payload.data.decode("utf-8")
        .strip()
    )
    connector = Connector()
    connection = None
    try:
        connection = connector.connect(
            args.instance,
            "pg8000",
            user=args.admin_user,
            password=secret,
            db=args.database,
            ip_type=IPTypes.PRIVATE if args.ip_type == "PRIVATE" else IPTypes.PUBLIC,
        )
        reference = (
            json.loads(args.schema_reference.read_text())
            if args.schema_reference
            else None
        )
        result = apply_migrations(
            connection,
            ordered_migrations(ROOT),
            reference=reference,
            adopt_through=args.adopt_through,
            source_bucket=args.source_bucket,
        )
        cursor = connection.cursor()
        try:
            for app_user in args.app_user:
                cursor.execute(
                    "SELECT set_config('thundercloud.app_user', %s, true)", (app_user,)
                )
                cursor.execute((ROOT / "scripts/db_grants.sql").read_text())
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
    finally:
        secret = ""
        if connection is not None:
            connection.close()
        connector.close()
    print(
        json.dumps(
            {"status": "succeeded", **result, "grants": "applied"}, sort_keys=True
        )
    )


if __name__ == "__main__":
    main()
