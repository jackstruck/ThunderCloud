"""Prepare an explicit next attempt only after observing terminal execution."""

from __future__ import annotations

import argparse
import hashlib
import json
from copy import deepcopy
from pathlib import Path

from maintenance.cli import atomic_json
from maintenance.cloud import classify, get_execution, read_json, session
from maintenance.durable import CloudArtifacts
from maintenance.rehearsal import file_checksum


def require_terminal(reference, api):
    execution = get_execution(api, reference["name"])
    if execution is None or execution.get("uid") != reference["uid"]:
        raise ValueError(
            "Previous execution is missing or replaced; resume is not authorized by this evidence"
        )
    if not execution.get("completionTime") or execution.get("runningCount", 0):
        raise ValueError(
            "Previous execution is not terminal; observe it instead of resuming"
        )
    return execution


def prepare(previous, original, directory, *, api=None, client=None):
    api = api or session()
    prior = {"name": previous["execution"], "uid": previous["execution_uid"]}
    execution = require_terminal(prior, api)
    if (
        original["execution_id"] != previous["execution_id"]
        or original["output_prefix"] != previous["output_prefix"]
        or not previous["image"].endswith("@" + original["image_digest"])
    ):
        raise ValueError(
            "Original request differs from the previous execution manifest"
        )
    store = CloudArtifacts(previous["output_prefix"], client=client)
    final = read_json(store.bucket, store.prefix, "report.json")
    blob = store.bucket.get_blob(store.prefix + "/progress.json")
    if blob is None:
        raise ValueError(
            "No prior progress exists to resume; prepare a separately reviewed execution"
        )
    raw = blob.download_as_bytes(if_generation_match=int(blob.generation), timeout=30)
    progress = json.loads(raw)
    result = classify(execution, final, progress, previous)
    if result["status"] == "succeeded":
        raise ValueError("Migration already succeeded; no resume is needed")
    if (
        progress.get("execution_id") != previous["execution_id"]
        or progress.get("cloud_run_execution")
        != previous["execution"].rsplit("/", 1)[1]
        or progress.get("image_digest") != original["image_digest"]
    ):
        raise ValueError("Prior progress does not belong to the recorded execution")
    request = deepcopy(original)
    request["resume_from"] = prior
    request["resume_progress"] = {
        "uri": previous["output_prefix"].rstrip("/") + "/progress.json",
        "generation": int(blob.generation),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
    }
    directory = Path(directory)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    destination = directory / "request.json"
    if destination.exists():
        raise ValueError("Resume preparation requires a new artifact directory")
    atomic_json(destination, request)
    return request


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resources", type=Path, required=True)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    args = parser.parse_args(argv)
    previous = json.loads(args.resources.read_text())
    if (
        file_checksum(args.request) != previous["request"]["sha256"]
        or args.request.stat().st_size != previous["request"]["bytes"]
    ):
        raise ValueError(
            "Original request does not match its recorded checksum and size"
        )
    prepare(previous, json.loads(args.request.read_text()), args.artifacts)
    print(
        json.dumps(
            {
                "request": str(args.artifacts / "request.json"),
                "remaining": ["stage this request and prepare a new dedicated job ID"],
            }
        )
    )


if __name__ == "__main__":
    main()
