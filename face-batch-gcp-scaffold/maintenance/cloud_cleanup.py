"""Export evidence and retire only the recorded terminal Cloud Run job."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath

from maintenance.cli import atomic_json
from maintenance.cloud import API, session
from maintenance.cloud_launch import contains
from maintenance.cloud_resume import require_terminal
from maintenance.durable import CloudArtifacts
from maintenance.rehearsal import file_checksum


def export(record, directory, execution, *, client=None):
    directory = Path(directory)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    store = CloudArtifacts(record["output_prefix"], client=client)
    exported = []
    for blob in store.bucket.list_blobs(prefix=store.prefix + "/"):
        relative = blob.name[len(store.prefix) + 1 :]
        path = PurePosixPath(relative)
        if path.is_absolute() or ".." in path.parts or str(path) != relative:
            raise ValueError("Remote artifact escapes its execution prefix")
        destination = directory / "objects" / relative
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not destination.resolve().is_relative_to(directory.resolve()):
            raise ValueError("Export path escapes its artifact directory")
        with destination.open("wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            blob.download_to_file(
                stream, if_generation_match=int(blob.generation), timeout=60
            )
            stream.flush()
            os.fsync(stream.fileno())
        if destination.stat().st_size != int(blob.size):
            raise ValueError("Exported artifact size differs from storage")
        exported.append(
            {
                "uri": "gs://" + store.bucket.name + "/" + blob.name,
                "generation": int(blob.generation),
                "sha256": file_checksum(destination),
                "bytes": int(blob.size),
                "local": str(destination),
            }
        )
    atomic_json(directory / "execution.json", execution)
    receipt = {
        "format": 1,
        "job": record["job"],
        "job_uid": record["job_uid"],
        "execution": record.get("execution"),
        "execution_uid": record.get("execution_uid"),
        "objects": exported,
        "execution_sha256": file_checksum(directory / "execution.json"),
        "retained": {
            "reports": record["output_prefix"],
            "request": record["request"],
            "image": record["image"],
            "owner": record["owner"],
            "deadline": record["retention_deadline"],
            "reason": "Recovery inputs and diagnostics remain available through the reviewed retention period",
        },
    }
    atomic_json(directory / "export-receipt.json", receipt)
    return receipt


def cleanup(path, directory, *, api=None, client=None, control=None, exporter=export):
    path = Path(path)
    record = json.loads(path.read_text())
    if record["stage"] not in {
        "create-requested",
        "submitted",
        "cleanup-review",
        "cleanup-requested",
        "cleaned",
    }:
        raise ValueError("Reconcile submission and its exact execution before cleanup")
    api = api or session()
    control = control or CloudArtifacts(
        record["control_prefix"], client=client, resume=True
    )
    control.generations["resources.json"] = record["control_generation"]

    def save():
        # This reference describes the final control object itself, so keep it
        # in the local receipt rather than recursively embedding it remotely.
        record.pop("cleanup_control_export", None)
        payload = json.dumps(record, sort_keys=True).encode()
        stored = control.write("resources.json", payload)
        record["control_generation"] = stored["generation"]
        if record["stage"] == "cleaned":
            destination = path.with_name("cleanup-control.json")
            with destination.open("wb") as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            record["cleanup_control_export"] = {
                "uri": record["control_prefix"].rstrip("/") + "/resources.json",
                "generation": stored["generation"],
                "local": str(destination),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "bytes": len(payload),
            }
        atomic_json(path, record)

    if record["stage"] in {"create-requested", "submitted"}:
        record["cleanup_origin_stage"] = record["stage"]
        record["stage"] = "cleanup-review"
        save()  # Fence a stale launcher before inspecting or deleting its job.

    response = api.get(API + record["job"], timeout=30)
    if response.status_code == 404:
        if not record.get("cleanup_export"):
            raise ValueError(
                "Job is missing without an export receipt; completion is unproven"
            )
        record.update(stage="cleaned", remaining=[])
        save()
        return record
    response.raise_for_status()
    job = response.json()
    if record.get("job_uid") not in (None, job["uid"]) or not contains(
        job, record["job_spec"]
    ):
        raise ValueError("Refusing cleanup of a replaced or changed job")
    record["job_uid"] = job["uid"]
    if job.get("reconciling"):
        raise ValueError("Job creation is still reconciling; observe before cleanup")
    execution = None
    if record.get("execution"):
        execution = require_terminal(
            {"name": record["execution"], "uid": record["execution_uid"]}, api
        )
    elif record.get("cleanup_origin_stage") != "create-requested":
        raise ValueError(
            "Run submission remains ambiguous; reconcile its execution first"
        )
    response = api.get(
        API + record["job"] + "/executions", params={"pageSize": 2}, timeout=30
    )
    response.raise_for_status()
    listing = response.json()
    if listing.get("nextPageToken") or [
        e["name"] for e in listing.get("executions", [])
    ] != ([record["execution"]] if execution else []):
        raise ValueError("Job has unrecorded executions; reconcile before cleanup")
    if record["stage"] == "cleanup-review":
        if execution is None:
            execution = {"status": "not-submitted", "job": job}
        record["cleanup_export"] = exporter(record, directory, execution, client=client)
        record["stage"] = "cleanup-requested"
        save()
    if not record.get("cleanup_export"):
        raise ValueError("Required export receipt is missing")
    if not job.get("deleteTime"):
        response = api.delete(
            API + record["job"], params={"etag": job["etag"]}, timeout=30
        )
        response.raise_for_status()
        record["delete_operation"] = response.json()["name"]
    record["remaining"] = [
        "Job deletion requested; rerun cleanup to verify disappearance",
        "Recovery inputs, image and reports retained through "
        + record["retention_deadline"],
    ]
    save()
    return record


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resources", type=Path, required=True)
    parser.add_argument("--exports", type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(cleanup(args.resources, args.exports), indent=2))


if __name__ == "__main__":
    main()
