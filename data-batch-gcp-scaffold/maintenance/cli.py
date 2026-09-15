"""Fixed maintenance procedures with durable local reports and heartbeats."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

import pg8000

from maintenance.migrations import (
    MigrationError,
    build_reference,
    digest,
    manifest,
    ordered_migrations,
)

ROOT = Path(__file__).resolve().parents[1]


def now():
    return datetime.now(UTC).isoformat()


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w") as stream:
        os.fchmod(stream.fileno(), 0o600)
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    descriptor = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class Report:
    def __init__(
        self, directory, execution, command, image, resume=False, artifact_store=None
    ):
        self.artifact_store = artifact_store
        self.directory = directory
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory.chmod(0o700)
        previous_path = directory / "report.json"
        if not previous_path.exists():
            previous_path = directory / "progress.json"
        previous = (
            json.loads(previous_path.read_text()) if previous_path.exists() else None
        )
        if previous is not None and not resume:
            raise RuntimeError(
                "Execution already has a report or progress; use a new execution identifier or explicit resume"
            )
        migrations = ordered_migrations(ROOT)
        if resume:
            if previous is None or any(
                previous.get(key) != expected
                for key, expected in {
                    "execution_id": execution,
                    "command": command,
                    "image_digest": image,
                    "manifest_checksum": digest(manifest(migrations)),
                }.items()
            ):
                raise RuntimeError(
                    "Resume execution, image or migration manifest differs"
                )
            history = directory / "attempts"
            history.mkdir(mode=0o700, exist_ok=True)
            atomic_json(
                history / f"attempt-{previous.get('attempt', 1)}.json", previous
            )
            (directory / "report.json").unlink(missing_ok=True)
        self.data = {
            "execution_id": execution,
            "cloud_run_execution": os.getenv("CLOUD_RUN_EXECUTION"),
            "attempt": previous.get("attempt", 1) + 1 if previous else 1,
            "command": command,
            "image_digest": image,
            "manifest_checksum": digest(manifest(migrations)),
            "status": "interrupted/incomplete",
            "stage": "starting",
            "started_at": now(),
            "heartbeat_at": now(),
            "last_committed_checkpoint": None,
            "checks": {},
            "remaining": ["procedure has not completed"],
        }
        build = ROOT / "build-manifest.json"
        self.data["build_fingerprint"] = (
            json.loads(build.read_text()) if build.exists() else None
        )
        self.update()

    def update(self, **values):
        self.data.update(values)
        self.data["heartbeat_at"] = now()
        atomic_json(self.directory / "progress.json", self.data)
        if self.artifact_store is not None:
            self.artifact_store.progress(self.data)
        print(
            json.dumps(
                {
                    k: self.data[k]
                    for k in [
                        "execution_id",
                        "stage",
                        "status",
                        "heartbeat_at",
                        "last_committed_checkpoint",
                    ]
                }
            ),
            flush=True,
        )

    def finish(self, status, remaining):
        self.update(status=status, remaining=remaining, completed_at=now())
        atomic_json(self.directory / "report.json", self.data)
        (self.directory / "report.md").write_text(
            f"# Maintenance {self.data['command']}\n\nExecution: {self.data['execution_id']}\n\n"
            f"Status: **{status}**\n\nStage: {self.data['stage']}\n\n"
            + "\n".join(f"- {item}" for item in remaining)
            + "\n"
        )

        for artifact in self.directory.rglob("*"):
            if artifact.is_file():
                artifact.chmod(0o600)
            elif artifact.is_dir():
                artifact.chmod(0o700)
        if self.artifact_store is not None:
            self.artifact_store.finish(self.directory)

    def run(self, stage, command, env=None, private_output=False):
        self.update(stage=stage)
        started = time.monotonic()
        with (self.directory / f"{stage}.log").open("w") as output:
            os.fchmod(output.fileno(), 0o600)
            if private_output:
                output.write(
                    "Private database procedure: command output suppressed; exit code is recorded in progress.\n"
                )
                output.flush()
            child = subprocess.Popen(
                command,
                cwd=ROOT,
                env=env,
                stdout=subprocess.DEVNULL if private_output else output,
                stderr=subprocess.DEVNULL if private_output else subprocess.STDOUT,
            )
            try:
                while True:
                    try:
                        code = child.wait(timeout=15)
                        break
                    except subprocess.TimeoutExpired:
                        self.update()
            except BaseException:
                child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
                raise
        self.data["checks"][stage] = {
            "exit_code": code,
            "duration_seconds": round(time.monotonic() - started, 3),
        }
        self.update()
        if code:
            raise RuntimeError(f"Mandatory stage failed: {stage}; see its durable log")


def validate(report, port):
    # This procedure is strictly local and synthetic. It cannot select a cloud
    # instance, take a URL, or run fixtures against a protected rehearsal copy.
    def connect(database="postgres"):
        return pg8000.connect(
            user="postgres", host="127.0.0.1", port=port, database=database
        )

    report.update(
        stage="fresh-schema",
        target={"host": "127.0.0.1", "port": port, "purpose": "disposable-validation"},
        starting_schema_version=None,
    )
    admin = connect()
    admin.autocommit = True
    reference_name = "reference_" + uuid.uuid4().hex
    cur = admin.cursor()
    cur.execute("SELECT count(*) FROM pg_tables WHERE schemaname='public'")
    if cur.fetchone()[0]:
        admin.close()
        raise RuntimeError(
            "Validation requires a new empty disposable PostgreSQL environment"
        )
    cur.execute(f'CREATE DATABASE "{reference_name}"')
    reference_db = None
    try:
        reference_db = connect(reference_name)
        reference = build_reference(reference_db, ordered_migrations(ROOT))
        atomic_json(report.directory / "schema-reference.json", reference)
        report.update(
            last_committed_checkpoint={
                "schema_version": max(int(v) for v in reference["schemas"])
            }
        )
    finally:
        if reference_db:
            reference_db.close()
        cur.execute(f'DROP DATABASE "{reference_name}" WITH (FORCE)')
        admin.close()
    environment = dict(
        os.environ,
        FACE_SUBJECT_TEST_PORT=str(port),
        FACE_BROWSER_TESTS="1",
        FACE_BROWSER_ARTIFACTS=str(report.directory / "browser"),
    )
    report.run(
        "tests",
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/unit",
            "tests/integration",
            "-p",
            "no:cacheprovider",
            "-ra",
            "--junitxml=" + str(report.directory / "tests.xml"),
        ],
        environment,
    )
    suites = ET.parse(report.directory / "tests.xml").getroot().iter("testsuite")
    totals = {key: 0 for key in ["tests", "failures", "errors", "skipped"]}
    for suite in suites:
        for key in totals:
            totals[key] += int(suite.get(key, "0"))
    report.data["checks"]["tests"].update(totals)
    if not totals["tests"] or any(totals[k] for k in ["failures", "errors", "skipped"]):
        raise RuntimeError("Mandatory tests failed, were missing, or skipped")
    report.run(
        "lint",
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--no-cache",
            "worker",
            "tests",
            "scripts",
            "maintenance",
        ],
    )
    report.run("javascript-app", ["node", "--check", "ui/app.js"])
    report.run("javascript-subjects", ["node", "--check", "ui/subjects.js"])
    report.update(
        stage="verified", schema_version=max(int(v) for v in reference["schemas"])
    )


def main(argv=None, *, artifact_store=None):
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=["validate", "rehearse-migration", "apply-migration"]
    )
    parser.add_argument("--execution-id", required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument(
        "--cloud-artifacts", help="Execution-scoped gs:// prefix for durable reports"
    )
    parser.add_argument("--image-digest", required=True)
    parser.add_argument("--postgres-port", type=int, default=5432)
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--backup-receipt", type=Path)
    parser.add_argument(
        "--source-bucket", help="Reviewed bucket for existing managed sources"
    )
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--plan-checksum")
    parser.add_argument("--rehearsal-receipt", type=Path)
    parser.add_argument("--schema-reference", type=Path)
    parser.add_argument("--before", type=Path)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "apply-migration" and not all(
        [
            args.plan,
            args.plan_checksum,
            args.rehearsal_receipt,
            args.schema_reference,
            args.before,
            args.backup,
            args.backup_receipt,
        ]
    ):
        parser.error(
            "apply-migration requires the reviewed plan/checksum, backup/receipt, rehearsal receipt, schema reference, and before receipt"
        )
    if args.command == "rehearse-migration" and (
        not args.backup or not args.backup_receipt
    ):
        parser.error("rehearse-migration requires --backup and --backup-receipt")
    if args.resume and args.command != "apply-migration":
        parser.error(
            "Explicit resume is supported for apply-migration; validation/rehearsal require a new isolated execution"
        )
    if args.cloud_artifacts and artifact_store is None:
        from maintenance.durable import CloudArtifacts

        artifact_store = CloudArtifacts(args.cloud_artifacts, resume=args.resume)
    report = Report(
        args.artifacts,
        args.execution_id,
        args.command,
        args.image_digest,
        resume=args.resume,
        artifact_store=artifact_store,
    )

    def interrupted(_signum, _frame):
        raise InterruptedError("Execution interrupted")

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        if args.command == "validate":
            validate(report, args.postgres_port)
        elif args.command == "rehearse-migration":
            from maintenance.rehearsal import rehearse

            rehearse(
                report,
                args.postgres_port,
                args.backup,
                args.backup_receipt,
                args.source_bucket,
            )
        else:
            from maintenance.apply import apply_command

            apply_command(report, args)
    except (InterruptedError, KeyboardInterrupt):
        report.finish(
            "interrupted/incomplete",
            ["Inspect logs and resource manifest before starting another execution"],
        )
        return 130
    except Exception as error:  # noqa: BLE001 — terminal report must survive any procedure error
        # Detailed test errors are synthetic and live in their protected logs.
        # Do not serialize database exception payloads into general progress.
        report.update(error_type=type(error).__name__)
        if isinstance(error, MigrationError):
            report.update(error_message=str(error))
        report.finish(
            "failed",
            [
                "Mandatory verification did not finish; inspect the stage log",
                "Temporary resources require scoped cleanup",
            ],
        )
        return 1
    report.finish(
        "succeeded",
        [
            "Export report checksums and run scoped cleanup",
            (
                "Application deployment and live acceptance remain"
                if args.command == "apply-migration"
                else "Production migration and acceptance are separate work"
            ),
        ],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
