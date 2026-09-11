"""Stage immutable migration inputs and record exact cloud object references."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path

import google_crc32c

from maintenance.cli import atomic_json
from maintenance.cloud_runner import INPUTS
from maintenance.durable import CloudArtifacts
from maintenance.migrations import digest


def fingerprints(path):
    sha, crc, size = hashlib.sha256(), google_crc32c.Checksum(), 0
    with Path(path).open("rb") as stream:
        while data := stream.read(1024 * 1024):
            sha.update(data)
            crc.update(data)
            size += len(data)
    return sha.hexdigest(), base64.b64encode(crc.digest()).decode(), size


def upload(store, name, path):
    path = Path(path)
    sha, crc, size = fingerprints(path)
    if not size:
        raise ValueError("Migration inputs cannot be empty")
    key = store.prefix + "/" + sha + "/" + name
    blob = store.bucket.get_blob(key)
    if blob is None:
        blob = store.bucket.blob(key)
        blob.metadata = {"sha256": sha}
        blob.upload_from_filename(
            str(path), if_generation_match=0, checksum="auto", timeout=60
        )
        blob.reload()
    if fingerprints(path) != (sha, crc, size):
        raise ValueError("Local migration input changed during staging")
    if (
        blob.crc32c != crc
        or int(blob.size) != size
        or (blob.metadata or {}).get("sha256") != sha
    ):
        raise ValueError("Staged object differs from the local migration input")
    return {
        "uri": "gs://" + store.bucket.name + "/" + key,
        "generation": int(blob.generation),
        "sha256": sha,
        "bytes": size,
    }


def stage(inputs, execution, input_prefix, output_prefix, directory, *, client=None):
    if set(inputs) != set(INPUTS):
        raise ValueError("All fixed migration inputs are required")
    paths = {name: Path(path).resolve(strict=True) for name, path in inputs.items()}
    plan = json.loads(paths["plan"].read_text())
    request = {
        "format": 1,
        "execution_id": execution,
        "image_digest": plan["image_digest"],
        "plan_checksum": digest(plan),
        "output_prefix": output_prefix,
        "inputs": {},
    }
    directory = Path(directory)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    store = CloudArtifacts(input_prefix, client=client)
    # Record intended names and local checksums before upload for interrupted handoff.
    manifest = {
        "format": 1,
        "input_prefix": input_prefix,
        "objects": {},
        "planned": {
            name: {"path": str(path), "sha256": fingerprints(path)[0]}
            for name, path in paths.items()
        },
    }
    previous = directory / "staging.json"
    if previous.exists():
        existing = json.loads(previous.read_text())
        if (
            existing["input_prefix"] != input_prefix
            or existing["planned"] != manifest["planned"]
        ):
            raise ValueError("Staging inputs changed; use a new artifact directory")
        manifest = existing
    atomic_json(directory / "staging.json", manifest)
    for name, path in paths.items():
        reference = upload(store, name, path)
        request["inputs"][name] = reference
        manifest["objects"][name] = reference
        atomic_json(directory / "staging.json", manifest)
    atomic_json(directory / "request.json", request)
    manifest["request_sha256"] = fingerprints(directory / "request.json")[0]
    atomic_json(directory / "staging.json", manifest)
    reference = upload(store, "request.json", directory / "request.json")
    manifest["objects"]["request"] = reference
    atomic_json(directory / "staging.json", manifest)
    atomic_json(directory / "request-reference.json", reference)
    return reference


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("inputs")
    p.add_argument("--inputs", type=Path, required=True)
    p.add_argument("--execution-id", required=True)
    p.add_argument("--output-prefix", required=True)
    p.add_argument("--input-prefix", required=True)
    p.add_argument("--artifacts", type=Path, required=True)
    p = sub.add_parser("request")
    p.add_argument("--request", type=Path, required=True)
    p.add_argument("--input-prefix", required=True)
    p.add_argument("--reference-output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "inputs":
        reference = stage(
            json.loads(args.inputs.read_text()),
            args.execution_id,
            args.input_prefix,
            args.output_prefix,
            args.artifacts,
        )
    else:
        reference = upload(
            CloudArtifacts(args.input_prefix), "request.json", args.request
        )
        atomic_json(args.reference_output, reference)
    print(json.dumps(reference, indent=2))


if __name__ == "__main__":
    main()
