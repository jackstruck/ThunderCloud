"""Durable operator handoff into ordinary runs; no archive queue or GPU writer."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import uuid
from contextlib import contextmanager
from pathlib import Path

from .run_repository import request_fingerprint
from .storage import validate_sha256


def validate_payload(payload):
    if set(payload) != {"handling_policy", "selection_policy", "source"}:
        raise ValueError("Explicit handling and selection policies are required")
    if payload["handling_policy"] not in {
        "search_then_discard",
        "enroll_only",
        "retain_and_enroll",
    }:
        raise ValueError("Unknown handling policy")
    if payload["selection_policy"] not in {"manual", "all_tracks"}:
        raise ValueError("Unknown selection policy")
    source = payload["source"]
    if (
        set(source)
        != {
            "kind",
            "bucket",
            "object_name",
            "generation",
            "sha256",
            "bytes",
            "content_type",
            "page_url",
        }
        or source["kind"] != "archive"
    ):
        raise ValueError("An exact archive reference is required")
    if not all(
        isinstance(source[key], str) and source[key].strip()
        for key in ["bucket", "object_name"]
    ):
        raise ValueError("Archive bucket and object name are required")
    for key in ["generation", "bytes"]:
        if type(source[key]) is not int or source[key] < 1:
            raise ValueError("Positive object generation and size are required")
    validate_sha256(source["sha256"])
    if source["content_type"] not in {"image/jpeg", "image/png", "video/mp4"}:
        raise ValueError("Unsupported archive media type")
    if source["page_url"] is not None and (
        not isinstance(source["page_url"], str)
        or not source["page_url"].startswith("https://")
    ):
        raise ValueError("External attribution must be an HTTPS page URL")


@contextmanager
def locked(path):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Stable sidecar inode: atomic receipt replacement must not replace the lock.
    with path.with_suffix(path.suffix + ".lock").open("a") as lock:
        os.fchmod(lock.fileno(), 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def save(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w") as stream:
        os.fchmod(stream.fileno(), 0o600)
        json.dump(value, stream, sort_keys=True, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    descriptor = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def prepare(path, principal, payload):
    validate_payload(payload)
    if not isinstance(principal, str) or not principal.strip():
        raise ValueError("Operator principal is required")
    with locked(path):
        if path.exists():
            raise ValueError(
                "Receipt already exists; submit it without recreating the handoff"
            )
        save(
            path,
            {
                "format": 1,
                "principal": principal,
                "payload": payload,
                "fingerprint": request_fingerprint(payload),
                "idempotency_key": str(uuid.uuid4()),
                "run_id": None,
            },
        )


def submit(path, repository):
    with locked(path):
        receipt = json.loads(path.read_text())
        validate_payload(receipt["payload"])
        if receipt["format"] != 1 or receipt["fingerprint"] != request_fingerprint(
            receipt["payload"]
        ):
            raise ValueError("Submission receipt changed")
        uuid.UUID(receipt["idempotency_key"])
        # Always use the original key, even if acknowledgement was lost after commit.
        result = repository.create(
            receipt["principal"], receipt["idempotency_key"], receipt["payload"]
        )
        if (
            receipt["run_id"] is not None
            and receipt["run_id"] != result.record["run_id"]
        ):
            raise ValueError(
                "Submission acknowledgement differs from the retained receipt"
            )
        receipt["run_id"] = result.record["run_id"]
        save(path, receipt)
        return result.record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    new = commands.add_parser("prepare")
    new.add_argument("--receipt", type=Path, required=True)
    for option in [
        "principal",
        "bucket",
        "object-name",
        "sha256",
        "content-type",
        "handling-policy",
        "selection-policy",
    ]:
        new.add_argument("--" + option, required=True)
    for option in ["generation", "bytes"]:
        new.add_argument("--" + option, type=int, required=True)
    new.add_argument("--page-url")
    send = commands.add_parser("submit")
    send.add_argument(
        "--receipt",
        type=Path,
        action="append",
        required=True,
        help="Repeat for an explicitly selected bulk submission",
    )
    send.add_argument("--active-run-limit", type=int, default=3)
    args = parser.parse_args()
    if args.command == "prepare":
        prepare(
            args.receipt,
            args.principal,
            {
                "handling_policy": args.handling_policy,
                "selection_policy": args.selection_policy,
                "source": {
                    "kind": "archive",
                    "bucket": args.bucket,
                    "object_name": args.object_name,
                    "generation": args.generation,
                    "sha256": args.sha256,
                    "bytes": args.bytes,
                    "content_type": args.content_type,
                    "page_url": args.page_url,
                },
            },
        )
        return
    from .config import Settings
    from .db import Database
    from .job_invoker import CloudRunJobInvoker
    from .run_repository import RunRepository

    if args.active_run_limit < 1:
        raise ValueError("Active run limit must be positive")
    settings = Settings.from_env()
    database = Database(
        settings.cloud_sql_instance,
        settings.db_user,
        settings.db_name,
        settings.cloud_sql_ip_type,
    )
    try:
        repository = RunRepository(database, active_run_limit=args.active_run_limit)
        for receipt in args.receipt:
            record = submit(receipt, repository)
            print(
                json.dumps({"run_id": record["run_id"], "state": record["state"]}),
                flush=True,
            )
        CloudRunJobInvoker(
            settings.project_id,
            os.getenv("FACE_REGION", "us-central1"),
            os.getenv("FACE_INGEST_JOB", "face-ingest-drain"),
            env={"FACE_INGEST_MODE": "drain"},
        )(record["run_id"])
    finally:
        database.close()


if __name__ == "__main__":
    main()
