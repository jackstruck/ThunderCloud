from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import uuid

from .config import Settings
from .db import Database
from .queue import QueueDatabase
from .storage import StorageRepository, load_configured_csek


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Manage face-processing Cloud Run rollouts"
    )
    sub = result.add_subparsers(dest="command", required=True)
    for name in ("status", "reconcile"):
        command = sub.add_parser(name)
        command.add_argument("--rollout-id", required=True)
    start = sub.add_parser("start")
    start.add_argument("--rollout-id", required=True)
    start.add_argument("--job", default="face-batch-gpu-drain")
    start.add_argument("--region", default="us-central1")
    start.add_argument("--project", default="teak-banner-dome")
    start.add_argument("--tasks", type=int, default=1)
    start.add_argument("--parallelism", type=int, default=1)
    return result


def _queue(settings: Settings) -> tuple[Database, QueueDatabase]:
    database = Database(
        settings.cloud_sql_instance,
        settings.db_user,
        settings.db_name,
        settings.cloud_sql_ip_type,
    )
    return database, QueueDatabase(database)


def _gcloud() -> tuple[str, dict[str, str]]:
    binary = os.getenv("FACE_GCLOUD_BIN") or shutil.which("gcloud")
    fallback = "/workspaces/ThunderCloud/google-cloud-sdk/bin/gcloud"
    if binary is None and os.path.isfile(fallback):
        binary = fallback
    if binary is None:
        raise RuntimeError("gcloud is not installed or configured")
    # The interactive SDK login may expire independently from ADC. Refresh ADC in
    # memory and give gcloud the short-lived token without placing it in arguments.
    import google.auth
    from google.auth.transport.requests import Request

    credentials, _ = google.auth.default()
    credentials.refresh(Request())
    environment = os.environ.copy()
    environment["CLOUDSDK_AUTH_ACCESS_TOKEN"] = credentials.token
    return binary, environment


def main(argv=None) -> None:
    args = parser().parse_args(argv)
    uuid.UUID(args.rollout_id)
    settings = Settings.from_env()
    settings.validate()
    database, queue = _queue(settings)
    try:
        if args.command == "status":
            output = queue.rollout_status(args.rollout_id)
        elif args.command == "reconcile":
            output = queue.reconcile(args.rollout_id)
            objects = output.pop("objects")
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
            lingering_staging = source_mismatch = 0
            for (
                source_uri,
                source_generation,
                source_bytes,
                staged_uri,
                staged_generation,
            ) in objects:
                generation, size = storage.verify_source(source_uri)
                if source_generation is not None and generation != int(
                    source_generation
                ):
                    source_mismatch += 1
                if source_bytes is not None and size != int(source_bytes):
                    source_mismatch += 1
                if staged_uri:
                    try:
                        storage.verify_staging(staged_uri, staged_generation)
                        lingering_staging += 1
                    except Exception as error:
                        if type(error).__name__ != "NotFound":
                            raise
            output["source_provenance_mismatches"] = source_mismatch
            output["lingering_staging_objects"] = lingering_staging
            output["reconciled"] = (
                output["status"] == "succeeded"
                and output["requested"] == output["succeeded"]
                and output["missing_committed_results"] == 0
                and source_mismatch == 0
                and lingering_staging == 0
            )
        else:
            if args.tasks < 1 or args.tasks > 10_000:
                raise ValueError("tasks must be between 1 and 10000")
            if args.parallelism != 1:
                raise ValueError(
                    "parallelism must remain 1 until matching concurrency is approved"
                )
            rollout = queue.rollout_status(args.rollout_id)
            if not rollout["matching_enabled"]:
                raise RuntimeError("rollout matching is not enabled")
            gcloud, environment = _gcloud()
            describe = subprocess.run(
                [
                    gcloud,
                    "run",
                    "jobs",
                    "describe",
                    args.job,
                    f"--project={args.project}",
                    f"--region={args.region}",
                    "--format=value(spec.template.spec.template.spec.containers[0].image)",
                ],
                check=True,
                capture_output=True,
                text=True,
                env=environment,
            )
            deployed_image = describe.stdout.strip()
            if deployed_image != rollout["image_digest"]:
                raise RuntimeError(
                    "deployed Cloud Run image does not match rollout digest"
                )
            queue.start_rollout(args.rollout_id)
            command = [
                gcloud,
                "run",
                "jobs",
                "execute",
                args.job,
                f"--project={args.project}",
                f"--region={args.region}",
                f"--tasks={args.tasks}",
                f"--update-env-vars=FACE_ROLLOUT_ID={args.rollout_id}",
                "--async",
                "--format=json",
            ]
            try:
                completed = subprocess.run(
                    command, check=True, capture_output=True, text=True, env=environment
                )
            except Exception:
                queue.launch_failed(args.rollout_id)
                raise
            execution = json.loads(completed.stdout)
            execution_name = execution["metadata"]["name"]
            queue.record_execution(args.rollout_id, execution_name)
            output = {
                "rollout_id": args.rollout_id,
                "execution_name": execution_name,
                "job": args.job,
                "region": args.region,
            }
        print(json.dumps(output, default=str, separators=(",", ":")))
    finally:
        database.close()


if __name__ == "__main__":
    main()
