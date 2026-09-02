#!/usr/bin/env python3
"""Apply additive schema and least-privilege grants without exposing DB credentials."""

from __future__ import annotations

import argparse
from pathlib import Path

from google.cloud import secretmanager
from google.cloud.sql.connector import Connector, IPTypes


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--project", default="teak-banner-dome")
    result.add_argument(
        "--instance", default="teak-banner-dome:us-central1:face-batch-pg"
    )
    result.add_argument("--database", default="face_index")
    result.add_argument("--admin-user", default="postgres")
    result.add_argument("--app-user", action="append", required=True)
    result.add_argument("--ip-type", choices=("PUBLIC", "PRIVATE"), default="PUBLIC")
    result.add_argument(
        "--password-secret", default="face-batch-postgres-admin-password"
    )
    result.add_argument(
        "--schema", type=Path, default=Path("scripts/db_schema.sql")
    )
    return result


def quote_identifier(value: str) -> str:
    if not value or "\x00" in value:
        raise ValueError("invalid PostgreSQL identifier")
    return '"' + value.replace('"', '""') + '"'


def main(argv=None) -> None:
    args = parser().parse_args(argv)
    password_client = secretmanager.SecretManagerServiceClient()
    secret = password_client.access_secret_version(
        request={
            "name": f"projects/{args.project}/secrets/{args.password_secret}/versions/latest"
        }
    ).payload.data.decode("utf-8").strip()
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
        cursor = connection.cursor()
        try:
            cursor.execute(args.schema.read_text(encoding="utf-8"))
            for app_user in args.app_user:
                principal = quote_identifier(app_user)
                cursor.execute(f"GRANT CONNECT ON DATABASE face_index TO {principal}")
                cursor.execute(f"GRANT USAGE ON SCHEMA public TO {principal}")
                cursor.execute(
                    "GRANT SELECT, INSERT, UPDATE, DELETE ON "
                    "identity, subject, source_asset, processing_job, face_track, "
                    f"processing_rollout, processing_work_item TO {principal}"
                )
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
    print("database schema and runtime grants applied")


if __name__ == "__main__":
    main()
