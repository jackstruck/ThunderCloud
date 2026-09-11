"""Resumable Cloud Run job creation and single execution submission."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import time
from pathlib import Path

from maintenance.cli import atomic_json
from maintenance.cloud import API, execution_image, session
from maintenance.durable import CloudArtifacts
from maintenance.migrations import digest


def prepare(config, request, reference, directory, image_index=None):
    if bool(request.get("resume_progress")) != bool(request.get("resume_from")):
        raise ValueError(
            "Resume requires both terminal execution evidence and pinned progress"
        )
    execution = request["execution_id"]
    if not re.fullmatch("[a-z0-9][a-z0-9-]{0,47}", execution):
        raise ValueError("Invalid maintenance execution ID")
    for key in ["project", "region", "job_id"]:
        if not re.fullmatch("[a-z][a-z0-9-]{0,62}", config[key]):
            raise ValueError("Invalid cloud resource identifier")
    if not config["image"].endswith("@" + request["image_digest"]) or not re.fullmatch(
        "sha256:[0-9a-f]{64}", request["image_digest"]
    ):
        raise ValueError("Launcher and reviewed request must pin the same image digest")
    if image_index is not None:
        execution_image({"image": config["image"], "image_index": image_index})
    for key in [
        "service_account",
        "network",
        "subnetwork",
        "encryption_key",
        "memory",
        "cpu",
    ]:
        if not isinstance(config[key], str) or not config[key]:
            raise ValueError(
                "Explicit identity, network, encryption and resource configuration is required"
            )
    if type(config["timeout_seconds"]) is not int or config["timeout_seconds"] <= 0:
        raise ValueError("Timeout must be chosen from rehearsal measurements")
    args = []
    for key in ["uri", "generation", "sha256", "bytes"]:
        args.extend(["--request-" + key, str(reference[key])])
    parent = f"projects/{config['project']}/locations/{config['region']}"
    job = parent + "/jobs/" + config["job_id"]
    spec = {
        "name": job,
        "labels": {
            "thundercloud-execution": execution,
            "thundercloud-purpose": "platform-maintenance",
        },
        "template": {
            "parallelism": 1,
            "taskCount": 1,
            "template": {
                "serviceAccount": config["service_account"],
                "maxRetries": 0,
                "timeout": str(config["timeout_seconds"]) + "s",
                "encryptionKey": config["encryption_key"],
                "vpcAccess": {
                    "networkInterfaces": [
                        {
                            "network": config["network"],
                            "subnetwork": config["subnetwork"],
                        }
                    ],
                    "egress": "PRIVATE_RANGES_ONLY",
                },
                "containers": [
                    {
                        "name": "maintenance",
                        "image": config["image"],
                        "command": ["python", "-m", "maintenance.cloud_runner"],
                        "args": args,
                        "resources": {
                            "limits": {"cpu": config["cpu"], "memory": config["memory"]}
                        },
                    }
                ],
            },
        },
    }
    directory = Path(directory)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    path = directory / "resources.json"
    if path.exists():
        raise ValueError("Use a new launcher resource directory")
    record = {
        "format": 1,
        "stage": "prepared",
        "execution_id": execution,
        "image": config["image"],
        "output_prefix": request["output_prefix"],
        "job": job,
        "job_spec": spec,
        "job_spec_checksum": digest(spec),
        "request": reference,
        "resume_from": request.get("resume_from"),
        "control_prefix": request["output_prefix"].rstrip("/")
        + "/control/"
        + config["job_id"],
        "control_generation": 0,
        "retention_deadline": config["retention_deadline"],
        "owner": config["owner"],
        "remaining": ["job not submitted"],
    }
    if image_index is not None:
        record["image_index"] = image_index
    atomic_json(path, record)
    return record


def contains(actual, expected):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            (k in actual and contains(actual[k], v))
            or (k == "maxRetries" and k not in actual and v == 0)
            for k, v in expected.items()
        )
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(actual) == len(expected)
            and all(contains(a, e) for a, e in zip(actual, expected, strict=True))
        )
    return actual == expected


def advance(path, *, api=None, store=None):
    path = Path(path)
    with path.with_suffix(".lock").open("a") as lock:
        os.fchmod(lock.fileno(), 0o600)
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        return _advance(path, api=api, store=store)


def _advance(path, *, api=None, store=None):
    path = Path(path)
    record = json.loads(path.read_text())
    if record["stage"] not in {
        "prepared",
        "create-requested",
        "run-requested",
        "submitted",
    }:
        raise ValueError(
            "Launcher state is retired or being cleaned; submission is closed"
        )
    if digest(record["job_spec"]) != record["job_spec_checksum"]:
        raise ValueError("Prepared job specification changed")
    store = store or CloudArtifacts(record["control_prefix"], resume=True)
    store.generations["resources.json"] = record["control_generation"]

    def save():
        exported = store.write(
            "resources.json", json.dumps(record, sort_keys=True).encode()
        )
        record["control_generation"] = exported["generation"]
        atomic_json(path, record)

    # Claim the observed remote generation before any create/run action.
    save()
    api = api or session()
    if record.get("resume_from") is not None and record["stage"] != "submitted":
        from maintenance.cloud_resume import require_terminal

        require_terminal(record["resume_from"], api)
    job = record["job"]
    if record["stage"] == "prepared":
        response = api.get(API + job, timeout=30)
        if response.status_code != 404:
            response.raise_for_status()
            raise ValueError(
                "Job name already exists; refusing to reuse unrelated resources"
            )
        record["stage"] = "create-requested"
        save()
        parent, job_id = job.rsplit("/jobs/", 1)
        response = api.post(
            API + parent + "/jobs",
            params={"jobId": job_id},
            # CreateJob takes the identifier in jobId; name is output-only on
            # this request, but remains part of subsequent identity checks.
            json={
                key: value for key, value in record["job_spec"].items() if key != "name"
            },
            timeout=30,
        )
        response.raise_for_status()
        record["create_operation"] = response.json()["name"]
        save()
    response = api.get(API + job, timeout=30)
    if response.status_code == 404:
        record["remaining"] = [
            "Job creation is not yet observable; do not resubmit blindly"
        ]
        save()
        return record
    response.raise_for_status()
    observed = response.json()
    if not contains(observed, record["job_spec"]):
        raise ValueError("Observed job differs from the prepared specification")
    if record.get("job_uid") not in (None, observed["uid"]):
        raise ValueError("Recorded job was replaced")
    record["job_uid"] = observed["uid"]
    if observed.get("reconciling", False):
        record["remaining"] = ["Job creation is still reconciling"]
        save()
        return record
    if record["stage"] == "create-requested":
        # Persist intent first. Lost acknowledgement is reconciled by listing this
        # dedicated job's executions, never by repeating the run request.
        record["stage"] = "run-requested"
        save()
        response = api.post(
            API + job + ":run", json={"etag": observed["etag"]}, timeout=30
        )
        response.raise_for_status()
        record["run_operation"] = response.json()["name"]
        save()
    response = api.get(API + job + "/executions", params={"pageSize": 2}, timeout=30)
    response.raise_for_status()
    listing = response.json()
    executions = listing.get("executions", [])
    if len(executions) > 1 or listing.get("nextPageToken"):
        raise ValueError(
            "Dedicated maintenance job has multiple executions; manual reconciliation required"
        )
    if executions:
        execution = executions[0]
        if not execution["name"].startswith(job + "/executions/"):
            raise ValueError("Execution belongs to another job")
        record.update(
            execution=execution["name"],
            execution_uid=execution["uid"],
            stage="submitted",
            remaining=[],
        )
    else:
        record["remaining"] = [
            "Run request has no observable execution yet; do not duplicate it"
        ]
    save()
    return record


def restore(prefix, directory, *, client=None):
    store = CloudArtifacts(prefix, client=client)
    blob = store.bucket.get_blob(store.prefix + "/resources.json")
    if blob is None:
        raise ValueError("No durable launcher state exists at this prefix")
    record = json.loads(
        blob.download_as_bytes(if_generation_match=int(blob.generation), timeout=30)
    )
    if (
        record["control_prefix"] != prefix.rstrip("/")
        or digest(record["job_spec"]) != record["job_spec_checksum"]
    ):
        raise ValueError(
            "Durable launcher state does not match its prefix or specification"
        )
    record["control_generation"] = int(blob.generation)
    directory = Path(directory)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    path = directory / "resources.json"
    if path.exists():
        raise ValueError("Restore into a new resource directory")
    atomic_json(path, record)
    return record


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare")
    for key in ["config", "request", "request-reference", "artifacts"]:
        p.add_argument("--" + key, type=Path, required=True)
    p.add_argument(
        "--image-index",
        type=Path,
        help="Exact published OCI index JSON, verified against the pinned digest",
    )
    p = sub.add_parser("start")
    p.add_argument("--resources", type=Path, required=True)
    p = sub.add_parser("restore")
    p.add_argument("--control-prefix", required=True)
    p.add_argument("--artifacts", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "prepare":
        from maintenance.rehearsal import file_checksum

        reference = json.loads(args.request_reference.read_text())
        if (
            file_checksum(args.request) != reference["sha256"]
            or args.request.stat().st_size != reference["bytes"]
        ):
            raise ValueError("Local request differs from its pinned reference")
        result = prepare(
            json.loads(args.config.read_text()),
            json.loads(args.request.read_text()),
            reference,
            args.artifacts,
            args.image_index.read_text() if args.image_index else None,
        )
    elif args.command == "restore":
        result = restore(args.control_prefix, args.artifacts)
    else:
        deadline = time.monotonic() + 55
        while True:
            result = advance(args.resources)
            if result["stage"] == "submitted" or time.monotonic() >= deadline:
                break
            time.sleep(2)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
