"""Execute a checksum-bound migration only after observed cutover preconditions."""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import pg8000

from maintenance.migrations import (
    LOCK,
    MigrationError,
    apply_migrations,
    digest,
    manifest,
    ordered_migrations,
)
from maintenance.preservation import capture, verify
from maintenance.rehearsal import file_checksum, verify_backup


@contextmanager
def connect_target(target):
    connection, connector = None, None
    try:
        if target["kind"] == "local":
            connection = pg8000.connect(
                host="127.0.0.1",
                port=target["port"],
                database=target["database"],
                user="postgres",
            )
        elif target["kind"] == "cloud-sql":
            from google.cloud import secretmanager
            from google.cloud.sql.connector import Connector, IPTypes

            # Credentials come from the existing runtime identity/Secret Manager.
            # Private connectivity is mandatory for the unattended cloud command.
            secret = (
                secretmanager.SecretManagerServiceClient()
                .access_secret_version(request={"name": target["password_secret"]})
                .payload.data.decode()
                .strip()
            )
            connector = Connector()
            connection = connector.connect(
                target["instance"],
                "pg8000",
                user=target["user"],
                password=secret,
                db=target["database"],
                ip_type=IPTypes.PRIVATE,
            )
            secret = ""
        else:
            raise MigrationError("Unsupported migration target kind")
        yield connection
    finally:
        if connection is not None:
            connection.close()
        if connector is not None:
            connector.close()


def preconditions(connection, plan):
    cursor = connection.cursor()
    try:
        cursor.execute("""SELECT current_database(),oid FROM pg_database
            WHERE datname=current_database()""")
        name, oid = cursor.fetchone()
        if name != plan["target"]["database"] or oid != plan["target"]["database_oid"]:
            raise MigrationError(
                "Connected database identity differs from the reviewed target"
            )
        roles = plan["application_roles"]
        if not roles or len(roles) != len(set(roles)):
            raise MigrationError("The reviewed application role inventory is required")
        cursor.execute(
            "SELECT rolname,rolcanlogin FROM pg_roles WHERE rolname=ANY(%s::text[])",
            (roles,),
        )
        observed = dict(cursor.fetchall())
        if set(observed) != set(roles) or any(observed.values()):
            raise MigrationError(
                "Application submissions are not paused: all reviewed roles must be NOLOGIN"
            )
        cursor.execute("""SELECT count(*) FROM pg_stat_activity WHERE datname=current_database()
            AND pid<>pg_backend_pid() AND backend_type='client backend'""")
        if cursor.fetchone()[0]:
            raise MigrationError(
                "Other database sessions remain; pause and drain before cutover"
            )
        queues = {
            "runs": "SELECT count(*) FROM media_run WHERE state NOT IN ('succeeded','failed','cancelled','expired')",
            "operations": "SELECT count(*) FROM run_operation WHERE state IN ('queued','leased')",
        }
        cursor.execute("SELECT to_regclass('public.processing_work_item')")
        if cursor.fetchone()[0] is not None:
            queues["archive_work"] = """SELECT count(*) FROM processing_work_item w
                JOIN processing_rollout r USING(rollout_id) WHERE w.state IN ('pending','leased','retry')
                AND NOT(w.state='retry' AND COALESCE(w.last_error_code,'')='OPERATOR_CANCELLED' AND r.status='cancelled')"""
        for name, query in queues.items():
            cursor.execute(query)
            if cursor.fetchone()[0]:
                raise MigrationError(
                    "Pending work requires drain or reviewed transfer: " + name
                )
        return {
            "target_identity": "verified",
            "application_logins": "paused",
            "client_sessions": 0,
            "pending_processing_work": 0,
        }
    finally:
        connection.rollback()
        cursor.close()


def apply_reviewed(
    report,
    connection,
    root,
    plan,
    plan_checksum,
    backup,
    backup_receipt,
    rehearsal_receipt,
    reference,
    before,
):
    from maintenance.cli import atomic_json

    if plan.get("format") != 1 or digest(plan) != plan_checksum:
        raise MigrationError("Reviewed plan checksum does not match")
    expires = datetime.fromisoformat(plan["valid_until"])
    if expires.tzinfo is None or expires <= datetime.now(UTC):
        raise MigrationError("Reviewed plan has expired")
    migrations = ordered_migrations(root)
    build = json.loads((root / "build-manifest.json").read_text())
    expected = {
        "image_digest": report.data["image_digest"],
        "source_checksum": build["source_checksum"],
        "migration_checksum": digest(manifest(migrations)),
        "reference_checksum": digest(reference),
        "backup_sha256": file_checksum(backup),
        "backup_receipt_checksum": digest(backup_receipt),
        "rehearsal_checksum": digest(rehearsal_receipt),
        "before_checksum": digest(before),
        "grants_checksum": file_checksum(root / "scripts/db_grants.sql"),
    }
    if any(plan.get(key) != value for key, value in expected.items()):
        raise MigrationError(
            "Target plan artifacts or image differ from the reviewed manifest"
        )
    verify_backup(backup, backup_receipt)
    if rehearsal_receipt.get("status") != "succeeded":
        raise MigrationError("A successful protected-copy rehearsal is required")
    if plan.get("source_bucket") != rehearsal_receipt.get("source_bucket"):
        raise MigrationError("Source bucket differs from the reviewed rehearsal")
    for key in [
        "image_digest",
        "source_checksum",
        "migration_checksum",
        "reference_checksum",
        "backup_sha256",
        "before_checksum",
    ]:
        if rehearsal_receipt.get(key) != expected[key]:
            raise MigrationError(
                "Rehearsal evidence does not match the reviewed migration: " + key
            )
    if plan["starting_version"] != backup_receipt["starting_version"]:
        raise MigrationError("Backup starting version differs from the plan")
    if plan["target"]["kind"] == "cloud-sql" and backup_receipt["source"] != {
        "instance": plan["target"]["instance"],
        "database": plan["target"]["database"],
    }:
        raise MigrationError("Backup belongs to another production target")
    state_file = report.directory / "apply-state.json"
    state = {"plan_checksum": plan_checksum, "target": plan["target"], **expected}
    if state_file.exists() and json.loads(state_file.read_text()) != state:
        raise MigrationError("Resume target or reviewed artifacts changed")
    atomic_json(state_file, state)
    cursor = connection.cursor()
    locked = False
    try:
        cursor.execute("SELECT pg_try_advisory_lock(%s,%s)", LOCK)
        locked = cursor.fetchone()[0]
        connection.commit()
        if not locked:
            raise MigrationError("Another migration execution holds the lock")
        report.update(
            stage="apply-preconditions",
            starting_schema_version=plan["starting_version"],
            plan_checksum=plan_checksum,
        )
        report.data["checks"]["cutover"] = preconditions(connection, plan)
        report.update(stage="apply-baseline-verification")
        verify(
            before,
            capture(
                connection,
                heartbeat=report.update,
                source_bucket=plan.get("source_bucket"),
            ),
        )
        report.update(stage="apply-migration")

        def checkpoint(migration, fingerprint):
            report.update(
                last_committed_checkpoint={
                    "version": migration.version,
                    "schema_fingerprint": fingerprint,
                }
            )

        result = apply_migrations(
            connection,
            migrations,
            reference=reference,
            adopt_through=plan["starting_version"],
            checkpoint=checkpoint,
            source_bucket=plan.get("source_bucket"),
        )
        after = capture(
            connection, heartbeat=report.update, source_bucket=plan.get("source_bucket")
        )
        atomic_json(report.directory / "after.json", after)
        report.data["checks"]["preservation"] = verify(before, after)
        if digest(after) != rehearsal_receipt["after_checksum"]:
            raise MigrationError(
                "Applied data differs from the reviewed rehearsal outcome"
            )
        report.update(stage="apply-grants")
        for role in plan["application_roles"]:
            cursor.execute(
                "SELECT set_config('thundercloud.app_user',%s,true)", (role,)
            )
            cursor.execute((root / "scripts/db_grants.sql").read_text())
            cursor.execute(
                """SELECT has_table_privilege(%s,'platform_schema_migration','INSERT,UPDATE,DELETE,TRUNCATE'),
                has_table_privilege(%s,'verified_embedding_model','INSERT,UPDATE,DELETE,TRUNCATE')""",
                (role, role),
            )
            if any(cursor.fetchone()):
                raise MigrationError(
                    "Application role inherits administrative ledger or provenance writes"
                )
        connection.commit()
        report.data["checks"]["migration"] = result
        report.update(stage="apply-verified", schema_version=result["version"])
    except BaseException:
        connection.rollback()
        raise
    finally:
        if locked:
            cursor.execute("SELECT pg_advisory_unlock(%s,%s)", LOCK)
            connection.commit()
        cursor.close()


def apply_command(report, args):
    root = Path(__file__).resolve().parents[1]
    plan = json.loads(args.plan.read_text())
    # Bind the reviewed plan before selecting a connection or loading credentials.
    if digest(plan) != args.plan_checksum:
        raise MigrationError("Reviewed plan checksum does not match")
    with connect_target(plan["target"]) as connection:
        apply_reviewed(
            report,
            connection,
            root,
            plan,
            args.plan_checksum,
            args.backup,
            json.loads(args.backup_receipt.read_text()),
            json.loads(args.rehearsal_receipt.read_text()),
            json.loads(args.schema_reference.read_text()),
            json.loads(args.before.read_text()),
        )
