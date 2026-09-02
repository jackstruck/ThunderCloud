from __future__ import annotations

import argparse
import json
import subprocess
import sys
import uuid
from pathlib import Path

from .config import Settings
from .db import Database, Versions
from .manifest import ManifestItem, read_manifest, select_items
from .queue import QueueDatabase
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
    remote_one = sub.add_parser("enqueue-object")
    remote_one.add_argument("object_uri")
    remote_one.add_argument("--sha256", required=True)
    remote_one.add_argument("--bytes", type=int, required=True)
    remote_one.add_argument("--generation", type=int)
    remote_one.add_argument("--content-type", default="video/mp4")
    remote_manifest = sub.add_parser("enqueue-manifest")
    remote_manifest.add_argument("--manifest", type=Path, required=True)
    remote_manifest.add_argument("--select-file", type=Path, required=True)
    for remote in (remote_one, remote_manifest):
        remote.add_argument("--name", required=True)
        remote.add_argument("--request-key")
        remote.add_argument("--image-digest", required=True)
        remote.add_argument("--creator-principal", required=True)
        remote.add_argument("--max-attempts", type=int, default=3)
        remote.add_argument("--worker-version", required=True)
        remote.add_argument("--detector-version", required=True)
        remote.add_argument("--embedding-model-version", required=True)
        remote.add_argument("--threshold-version", required=True)
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
    if args.command == "enqueue-object":
        base = _exact_item(args.object_uri, args.sha256, settings)
        return [
            ManifestItem(
                base.uid,
                base.object_uri,
                base.sha256,
                args.bytes,
                args.content_type,
                None,
                args.generation,
                {},
            )
        ]
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
    selected = _selected(args, settings)
    if args.command.startswith("enqueue-"):
        if not settings.matching_enabled:
            raise ValueError("remote rollouts require FACE_MATCHING_ENABLED=true")
        database = Database(
            settings.cloud_sql_instance,
            settings.db_user,
            settings.db_name,
            settings.cloud_sql_ip_type,
        )
        try:
            rollout_id, count = QueueDatabase(database).create_rollout(
                name=args.name,
                request_key=args.request_key,
                image_digest=args.image_digest,
                versions=Versions(
                    args.worker_version,
                    args.detector_version,
                    args.embedding_model_version,
                    args.threshold_version,
                ),
                creator_principal=args.creator_principal,
                configuration={
                    "detector_fps": settings.detector_fps,
                    "best_n": settings.best_n,
                    "top_k": settings.top_k,
                    "match_threshold": settings.match_threshold,
                    "matching_enabled": True,
                },
                items=selected,
                max_attempts=args.max_attempts,
            )
        finally:
            database.close()
        print(json.dumps({"rollout_id": rollout_id, "requested_count": count}, separators=(",", ":")))
        return

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
    for item in selected:
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
