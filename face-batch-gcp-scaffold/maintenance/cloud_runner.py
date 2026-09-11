"""Cloud Run entry point for a reviewed, immutable migration input bundle."""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

from maintenance.durable import CloudArtifacts
from maintenance.rehearsal import file_checksum

INPUTS = (
    "plan",
    "backup",
    "backup-receipt",
    "rehearsal-receipt",
    "schema-reference",
    "before",
)


def download(client, reference, destination):
    if set(reference) != {"uri", "generation", "sha256", "bytes"}:
        raise ValueError("Input requires URI, generation, checksum and size")
    uri = reference["uri"]
    if not isinstance(uri, str) or not uri.startswith("gs://"):
        raise ValueError("Input must be a GCS object")
    bucket, separator, name = uri[5:].partition("/")
    if not bucket or not separator or not name:
        raise ValueError("Input must identify an object")
    if type(reference["generation"]) is not int or reference["generation"] <= 0:
        raise ValueError("Input requires an exact positive generation")
    if type(reference["bytes"]) is not int or reference["bytes"] <= 0:
        raise ValueError("Input requires a positive byte count")
    if not isinstance(reference["sha256"], str) or not re.fullmatch(
        "[0-9a-f]{64}", reference["sha256"]
    ):
        raise ValueError("Input requires a SHA-256 checksum")
    destination = Path(destination)
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = destination.with_suffix(".partial")
    blob = client.bucket(bucket).blob(name, generation=reference["generation"])
    try:
        with temporary.open("wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            blob.download_to_file(
                stream, if_generation_match=reference["generation"], timeout=60
            )
            stream.flush()
            os.fsync(stream.fileno())
        if (
            temporary.stat().st_size != reference["bytes"]
            or file_checksum(temporary) != reference["sha256"]
        ):
            raise ValueError(
                "Downloaded input does not match its reviewed checksum and size"
            )
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def run(request, work, *, client=None, invoke=None):
    from google.cloud import storage

    from maintenance.cli import main

    client = client or storage.Client()
    invoke = invoke or main
    if request.get("format") != 1 or set(request.get("inputs", {})) != set(INPUTS):
        raise ValueError("A complete reviewed migration bundle is required")
    if not re.fullmatch("[a-z0-9][a-z0-9-]{0,47}", request["execution_id"]):
        raise ValueError("Invalid maintenance execution identifier")
    if not re.fullmatch("sha256:[0-9a-f]{64}", request["image_digest"]):
        raise ValueError("A pinned migration image digest is required")
    work = Path(work)
    work.mkdir(mode=0o700, parents=True, exist_ok=True)
    work.chmod(0o700)
    inputs, reports = work / "inputs", work / "reports"
    reports.mkdir(mode=0o700, exist_ok=True)
    previous = request.get("resume_progress")
    store = CloudArtifacts(
        request["output_prefix"], client=client, resume=previous is not None
    )
    if previous is not None:
        if previous["uri"] != request["output_prefix"].rstrip("/") + "/progress.json":
            raise ValueError("Resume progress must belong to this output prefix")
        download(client, previous, reports / "progress.json")
        store.generations["progress.json"] = previous["generation"]
    for name in INPUTS:
        download(client, request["inputs"][name], inputs / name)
    argv = [
        "apply-migration",
        "--execution-id",
        request["execution_id"],
        "--image-digest",
        request["image_digest"],
        "--artifacts",
        str(reports),
        "--plan-checksum",
        request["plan_checksum"],
    ]
    for name in INPUTS:
        argv.extend(["--" + name, str(inputs / name)])
    if previous is not None:
        argv.append("--resume")
    return invoke(argv, artifact_store=store)


def main(argv=None):
    from google.cloud import storage

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request-uri", required=True)
    parser.add_argument("--request-generation", required=True, type=int)
    parser.add_argument("--request-sha256", required=True)
    parser.add_argument("--request-bytes", required=True, type=int)
    parser.add_argument("--work", type=Path, default=Path("/tmp/maintenance"))
    args = parser.parse_args(argv)
    client = storage.Client()
    request_path = args.work / "request.json"
    download(
        client,
        {
            "uri": args.request_uri,
            "generation": args.request_generation,
            "sha256": args.request_sha256,
            "bytes": args.request_bytes,
        },
        request_path,
    )
    return run(json.loads(request_path.read_text()), args.work, client=client)


if __name__ == "__main__":
    raise SystemExit(main())
