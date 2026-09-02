from __future__ import annotations

import argparse
import json
import subprocess
import sys
import uuid
from pathlib import Path

from .config import Settings
from .manifest import ManifestItem, read_manifest, select_items
from .storage import GcsUri, StorageRepository, load_configured_csek, validate_sha256


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Select, CSEK-stage, and locally process face videos"
    )
    sub = result.add_subparsers(dest="command", required=True)
    one = sub.add_parser("submit-object")
    one.add_argument("object_uri")
    one.add_argument("--sha256")
    manifest = sub.add_parser("submit-manifest")
    manifest.add_argument("--manifest", type=Path, required=True)
    manifest.add_argument("--select-file", type=Path, required=True)
    return result


def _exact_item(uri: str, sha256: str | None, settings: Settings) -> ManifestItem:
    parsed = GcsUri.parse(uri)
    if parsed.bucket != settings.bucket or not parsed.object_name.startswith(
        settings.source_prefix
    ):
        raise ValueError("source object is outside the configured source prefix")
    return ManifestItem(
        Path(parsed.object_name).stem,
        uri,
        validate_sha256(sha256),
        None,
        None,
        None,
        None,
        {},
    )


def _selected(args, settings: Settings) -> list[ManifestItem]:
    if args.command == "submit-object":
        return [_exact_item(args.object_uri, args.sha256, settings)]
    records = read_manifest(args.manifest, settings.bucket, settings.source_prefix)
    selectors = args.select_file.read_text(encoding="utf-8").splitlines()
    return select_items(records, selectors)


def _metadata(item: ManifestItem, request_id: str) -> dict[str, str]:
    result = {
        "external-source-ref": item.object_uri,
        "request-id": request_id,
        "source-uid": item.uid,
    }
    if item.sha256:
        result["sha256"] = item.sha256
    if item.timestamp:
        result["source-timestamp"] = item.timestamp
    return result


def _source_metadata(item: ManifestItem) -> dict:
    return {
        "uid": item.uid,
        "generation": item.generation,
        "bytes": item.bytes,
        "content_type": item.content_type,
        "manifest_timestamp": item.timestamp,
        **item.source,
    }


def main(argv=None) -> None:
    args = parser().parse_args(argv)
    settings = Settings.from_env()
    settings.validate()
    csek = load_configured_csek(
        settings.project_id, settings.csek_file, settings.csek_secret
    )
    storage = StorageRepository(
        settings.project_id,
        settings.bucket,
        settings.source_prefix,
        settings.staging_prefix,
        csek,
    )
    results = []
    for item in _selected(args, settings):
        request_id = str(uuid.uuid4())
        staging_uri, generation, size = storage.stage(
            item.object_uri, _metadata(item, request_id)
        )
        job_id = str(uuid.uuid4())
        command = [
            sys.executable,
            "-m",
            "worker",
            "--gcs-uri",
            staging_uri,
            "--external-source-ref",
            item.object_uri,
            "--job-id",
            job_id,
            "--staging-generation",
            str(generation),
            "--source-metadata-json",
            json.dumps(_source_metadata(item), separators=(",", ":")),
        ]
        if item.sha256:
            command.extend(["--expected-sha256", item.sha256])
        # No secret value is placed in the subprocess command or output.
        completed = subprocess.run(command, check=False)
        results.append(
            {
                "job_id": job_id,
                "source": item.object_uri,
                "staging_generation": generation,
                "staging_bytes": size,
                "status": "succeeded" if completed.returncode == 0 else "failed",
            }
        )
        if completed.returncode != 0:
            print(json.dumps(results, separators=(",", ":")))
            raise SystemExit(completed.returncode)
    print(json.dumps(results, separators=(",", ":")))


if __name__ == "__main__":
    main()
