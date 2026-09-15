"""Restore a protected copy, migrate it, and independently exercise recovery."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from pathlib import Path

import pg8000

from maintenance.migrations import (
    MigrationError,
    apply_migrations,
    build_reference,
    digest,
    manifest,
    ordered_migrations,
    schema_fingerprint,
)
from maintenance.preservation import capture, verify

ROOT = Path(__file__).resolve().parents[1]


def file_checksum(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while block := stream.read(1024 * 1024):
            result.update(block)
    return result.hexdigest()


def verify_backup(backup, receipt):
    required = {
        "format",
        "sha256",
        "bytes",
        "starting_version",
        "source",
        "captured_at",
    }
    if not required <= receipt.keys() or receipt["format"] != 1:
        raise MigrationError("Backup receipt is incomplete")
    if receipt["bytes"] != backup.stat().st_size or receipt["sha256"] != file_checksum(
        backup
    ):
        raise MigrationError("Protected backup does not match its receipt")
    if type(receipt["starting_version"]) is not int or receipt["starting_version"] < 10:
        raise MigrationError(
            "Preservation verification requires the unified-example baseline (010 or later)"
        )
    with backup.open("rb") as stream:
        if stream.read(5) != b"PGDMP":
            raise MigrationError("A PostgreSQL custom-format backup is required")


def rehearse(report, port, backup, backup_receipt, source_bucket=None):
    from maintenance.cli import atomic_json

    receipt = json.loads(backup_receipt.read_text())
    verify_backup(backup, receipt)
    migrations = ordered_migrations(ROOT)
    if receipt["starting_version"] >= len(migrations):
        raise MigrationError("Backup is newer than this migration image")
    report.update(
        stage="rehearsal-preflight",
        backup_checksum=receipt["sha256"],
        starting_schema_version=receipt["starting_version"],
        source_bucket=source_bucket,
        target={"host": "127.0.0.1", "port": port, "purpose": "isolated-rehearsal"},
    )

    def connect(name="postgres"):
        return pg8000.connect(
            user="postgres", host="127.0.0.1", port=port, database=name
        )

    admin = connect()
    admin.autocommit = True
    cur = admin.cursor()
    cur.execute("SELECT count(*) FROM pg_tables WHERE schemaname='public'")
    if cur.fetchone()[0]:
        admin.close()
        raise MigrationError("Rehearsal requires a new isolated PostgreSQL environment")
    names = {
        kind: "rehearsal_" + uuid.uuid4().hex for kind in ["fresh", "copy", "recovery"]
    }
    resources = {
        "purpose": "isolated-rehearsal",
        "databases": [],
        "retention": "Disposable compute only; protected input backup and output reports are retained",
    }
    resource_path = report.directory / "rehearsal-databases.json"
    atomic_json(resource_path, resources)
    try:
        for kind, name in names.items():
            # Record the exact intended object before mutation for crash cleanup.
            resources["databases"].append(
                {"name": name, "kind": kind, "state": "planned"}
            )
            atomic_json(resource_path, resources)
            cur.execute(f'CREATE DATABASE "{name}"')
            resources["databases"][-1]["state"] = "created"
            atomic_json(resource_path, resources)
        fresh = connect(names["fresh"])
        try:
            report.update(stage="rehearsal-reference")
            reference = build_reference(fresh, migrations)
            atomic_json(report.directory / "schema-reference.json", reference)
        finally:
            fresh.close()
        # Never run pytest or user-provided commands on these targets. Both
        # pg_restore calls are fixed and restricted to loopback on the isolated DB.
        for kind in ["copy", "recovery"]:
            report.run(
                "restore-" + kind,
                [
                    "pg_restore",
                    "--exit-on-error",
                    "--single-transaction",
                    "--no-owner",
                    "--no-privileges",
                    "--host=127.0.0.1",
                    f"--port={port}",
                    "--username=postgres",
                    "--dbname=" + names[kind],
                    str(backup),
                ],
                private_output=True,
            )
        report.update(stage="rehearsal-baseline-verification")
        restored = connect(names["copy"])
        try:
            before = capture(
                restored, heartbeat=report.update, source_bucket=source_bucket
            )
            atomic_json(report.directory / "before.json", before)
            verify(before, before)  # Pre-existing invariant failures are not excused.
            starting_schema = schema_fingerprint(restored.cursor())
            restored.rollback()
            if (
                starting_schema
                != reference["schemas"][str(receipt["starting_version"])]
            ):
                raise MigrationError(
                    "Backup schema differs from its declared migration version"
                )
            report.update(stage="rehearsal-migrate")
            started = time.monotonic()

            def checkpoint(m, fingerprint):
                report.update(
                    last_committed_checkpoint={
                        "version": m.version,
                        "schema_fingerprint": fingerprint,
                    }
                )

            result = apply_migrations(
                restored,
                migrations,
                reference=reference,
                adopt_through=receipt["starting_version"],
                checkpoint=checkpoint,
                source_bucket=source_bucket,
            )
            after = capture(
                restored, heartbeat=report.update, source_bucket=source_bucket
            )
            atomic_json(report.directory / "after.json", after)
            checks = verify(before, after)
            report.data["checks"]["migration"] = {
                **result,
                "duration_seconds": round(time.monotonic() - started, 3),
                **checks,
            }
        finally:
            restored.close()
        recovered = connect(names["recovery"])
        try:
            report.update(stage="recovery-resume")
            verify(
                before,
                capture(
                    recovered, heartbeat=report.update, source_bucket=source_bucket
                ),
            )
            # Commit baseline adoption only, close the connection, and resume
            # against the same target/manifest. Production uses this same ledger.
            apply_migrations(
                recovered,
                migrations[: receipt["starting_version"] + 1],
                reference=reference,
                adopt_through=receipt["starting_version"],
            )
            recovered.close()
            recovered = connect(names["recovery"])
            resumed = apply_migrations(
                recovered, migrations, reference=reference, source_bucket=source_bucket
            )
            verify(
                after,
                capture(
                    recovered, heartbeat=report.update, source_bucket=source_bucket
                ),
            )
            report.data["checks"]["recovery"] = {"status": "succeeded", **resumed}
        finally:
            recovered.close()
        atomic_json(
            report.directory / "rehearsal-receipt.json",
            {
                "format": 1,
                "status": "succeeded",
                "source_bucket": source_bucket,
                "backup_sha256": receipt["sha256"],
                "source": receipt["source"],
                "starting_version": receipt["starting_version"],
                "migration_manifest": manifest(migrations),
                "migration_checksum": digest(manifest(migrations)),
                "reference_checksum": digest(reference),
                "before_checksum": digest(before),
                "after_checksum": digest(after),
                "image_digest": report.data["image_digest"],
                "source_checksum": report.data["build_fingerprint"]["source_checksum"]
                if report.data["build_fingerprint"]
                else None,
            },
        )
        report.update(stage="rehearsal-verified", schema_version=migrations[-1].version)
    finally:
        # Data used for diagnosis/recovery remains in the protected input backup;
        # stage reports and before/after digests are exported before compute cleanup.
        for resource in resources["databases"]:
            cur.execute(f'DROP DATABASE IF EXISTS "{resource["name"]}" WITH (FORCE)')
            resource["state"] = "removed"
            atomic_json(resource_path, resources)
        admin.close()
