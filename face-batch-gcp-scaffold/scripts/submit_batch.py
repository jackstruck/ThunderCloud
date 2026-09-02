#!/usr/bin/env python3
"""Stage selected manifest videos and run a bounded Google Cloud Batch rollout."""

from __future__ import annotations

import argparse
import json
import re
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import google.auth
from google.auth.transport.requests import AuthorizedSession

from worker.manifest import ManifestItem, read_manifest
from worker.storage import StorageRepository, load_csek

TERMINAL_STATES = {"SUCCEEDED", "FAILED", "DELETION_IN_PROGRESS"}


@dataclass
class Submission:
    uid: str
    source_uri: str
    source_sha256: str | None
    application_job_id: str
    batch_job_id: str
    batch_job_name: str | None = None
    staging_uri: str | None = None
    staging_generation: int | None = None
    state: str = "SELECTED"
    failure: str | None = None


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--max-in-flight", type=int, default=2)
    parser.add_argument("--exclude-uid", action="append", default=[])
    parser.add_argument("--state-file", type=Path, required=True)
    parser.add_argument("--csek-file", type=Path, required=True)
    parser.add_argument("--project", default="teak-banner-dome")
    parser.add_argument("--region", default="us-central1")
    parser.add_argument("--bucket", default="teak-banner-dome-bulk-videos")
    parser.add_argument("--source-prefix", default="videos/")
    parser.add_argument("--staging-prefix", default="face-staging/")
    parser.add_argument(
        "--image",
        default=(
            "us-central1-docker.pkg.dev/teak-banner-dome/face-batch-worker/"
            "worker@sha256:5da8096026d0ed126fdd99a7f2d04e8e3b4d09fd47c87fb372495054b1f0cd69"
        ),
    )
    parser.add_argument(
        "--service-account",
        default="face-batch-runtime@teak-banner-dome.iam.gserviceaccount.com",
    )
    parser.add_argument("--network", default="face-batch-vpc")
    parser.add_argument("--subnetwork", default="face-batch-batch")
    parser.add_argument("--machine-type", default="g2-standard-8")
    parser.add_argument("--poll-seconds", type=int, default=20)
    parser.add_argument("--max-run-seconds", type=int, default=21600)
    parser.add_argument("--on-demand", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if not 1 <= args.count <= 20:
        parser.error("--count must be between 1 and 20")
    if not 1 <= args.max_in_flight <= 4:
        parser.error("--max-in-flight must be between 1 and 4")
    return args


def select_controlled(items: list[ManifestItem], count: int, excluded: set[str]):
    selected = [item for item in items if item.uid not in excluded][:count]
    if len(selected) != count:
        raise ValueError(f"manifest contains only {len(selected)} eligible selections")
    return selected


def source_metadata(item: ManifestItem) -> dict:
    return {
        "uid": item.uid,
        "generation": item.generation,
        "bytes": item.bytes,
        "content_type": item.content_type,
        "manifest_timestamp": item.timestamp,
        **item.source,
    }


def staging_metadata(item: ManifestItem, application_job_id: str) -> dict[str, str]:
    result = {
        "external-source-ref": item.object_uri,
        "request-id": application_job_id,
        "source-uid": item.uid,
    }
    if item.sha256:
        result["sha256"] = item.sha256
    return result


def build_job(args, item: ManifestItem, submission: Submission) -> dict:
    if not args.image.startswith(
        f"{args.region}-docker.pkg.dev/{args.project}/"
    ) or "@sha256:" not in args.image:
        raise ValueError("Batch image must be an immutable digest in the project registry")
    commands = [
        "--gcs-uri",
        submission.staging_uri,
        "--external-source-ref",
        item.object_uri,
        "--job-id",
        submission.application_job_id,
        "--staging-generation",
        str(submission.staging_generation),
        "--source-metadata-json",
        json.dumps(source_metadata(item), separators=(",", ":")),
    ]
    if item.sha256:
        commands.extend(["--expected-sha256", item.sha256])
    environment = {
        "FACE_PROJECT_ID": args.project,
        "FACE_BUCKET": args.bucket,
        "FACE_SOURCE_PREFIX": args.source_prefix,
        "FACE_STAGING_PREFIX": args.staging_prefix,
        "FACE_CSEK_SECRET": "face-batch-gcs-csek",
        "FACE_CLOUD_SQL_INSTANCE": f"{args.project}:{args.region}:face-batch-pg",
        "FACE_CLOUD_SQL_IP_TYPE": "PRIVATE",
        "FACE_DB_USER": f"face-batch-runtime@{args.project}.iam",
        "FACE_DB_NAME": "face_index",
        "FACE_MATCHING_ENABLED": "true",
        "FACE_MATCH_THRESHOLD": "0.55",
        "FACE_THRESHOLD_VERSION": "controlled-eval-0p55-v1",
        "FACE_WORKER_VERSION": "0.1.0-r3-cuda13",
    }
    provisioning = "STANDARD" if args.on_demand else "SPOT"
    return {
        "taskGroups": [
            {
                "taskCount": "1",
                "parallelism": "1",
                "taskSpec": {
                    "runnables": [
                        {
                            "container": {
                                "imageUri": args.image,
                                "entrypoint": "/opt/venv/bin/python",
                                "commands": ["/app/scripts/verify_gpu_image.py"],
                            }
                        },
                        {
                            "container": {
                                "imageUri": args.image,
                                "entrypoint": "/opt/venv/bin/python",
                                "commands": ["-m", "worker", *commands],
                            }
                        },
                    ],
                    "environment": {"variables": environment},
                    "computeResource": {"cpuMilli": "8000", "memoryMib": "30000"},
                    "maxRetryCount": 2,
                    "maxRunDuration": f"{args.max_run_seconds}s",
                },
            }
        ],
        "allocationPolicy": {
            "instances": [
                {
                    "policy": {
                        "machineType": args.machine_type,
                        "provisioningModel": provisioning,
                    },
                    "installGpuDrivers": True,
                }
            ],
            "network": {
                "networkInterfaces": [
                    {
                        "network": f"projects/{args.project}/global/networks/{args.network}",
                        "subnetwork": (
                            f"projects/{args.project}/regions/{args.region}/"
                            f"subnetworks/{args.subnetwork}"
                        ),
                        "noExternalIpAddress": False,
                    }
                ]
            },
            "serviceAccount": {"email": args.service_account},
        },
        "logsPolicy": {"destination": "CLOUD_LOGGING"},
        "labels": {"workload": "face-batch", "rollout": "controlled"},
    }


def persist(path: Path, submissions: list[Submission]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(
            {
                "updated_at": datetime.now(UTC).isoformat(),
                "submissions": [asdict(item) for item in submissions],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def sanitize_failure(job: dict) -> str:
    events = job.get("status", {}).get("statusEvents", [])
    message = next(
        (
            event.get("description", "")
            for event in reversed(events)
            if event.get("description")
        ),
        "Batch job failed without a status description",
    )
    return re.sub(r"gs://[^\s]+", "[gcs-object]", message)[:500]


def infrastructure_blocker(job: dict) -> str | None:
    descriptions = [
        event.get("description", "")
        for event in job.get("status", {}).get("statusEvents", [])
    ]
    joined = " ".join(descriptions)
    blockers = {
        "CODE_GCE_QUOTA_EXCEEDED": "GPU quota exceeded",
        "CODE_GCE_ZONE_RESOURCE_POOL_EXHAUSTED": "GPU resource pool exhausted",
    }
    found = [label for code, label in blockers.items() if code in joined]
    return "; ".join(found) if found else None


def submit(session: AuthorizedSession, args, body: dict, batch_job_id: str) -> dict:
    endpoint = (
        f"https://batch.googleapis.com/v1/projects/{args.project}/locations/"
        f"{args.region}/jobs"
    )
    response = session.post(endpoint, params={"jobId": batch_job_id}, json=body)
    response.raise_for_status()
    return response.json()


def get_job(session: AuthorizedSession, name: str) -> dict:
    response = session.get(f"https://batch.googleapis.com/v1/{name}")
    response.raise_for_status()
    return response.json()


def delete_job(session: AuthorizedSession, name: str, reason: str) -> None:
    response = session.delete(
        f"https://batch.googleapis.com/v1/{name}", params={"reason": reason}
    )
    response.raise_for_status()


def main(argv=None) -> None:
    args = parse_args(argv)
    items = read_manifest(args.manifest, args.bucket, args.source_prefix)
    selected = select_controlled(items, args.count, set(args.exclude_uid))
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    submissions = [
        Submission(
            uid=item.uid,
            source_uri=item.object_uri,
            source_sha256=item.sha256,
            application_job_id=str(uuid.uuid4()),
            batch_job_id=f"face-ctl-{stamp}-{index:02d}",
        )
        for index, item in enumerate(selected, 1)
    ]
    persist(args.state_file, submissions)
    if args.dry_run:
        print(json.dumps({"selected": [item.uid for item in submissions]}))
        return

    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    session = AuthorizedSession(credentials)
    storage = StorageRepository(
        args.project,
        args.bucket,
        args.source_prefix,
        args.staging_prefix,
        load_csek(args.csek_file),
    )
    item_by_uid = {item.uid: item for item in selected}
    pending = list(submissions)
    active: list[Submission] = []
    stop_launching = False

    while pending or active:
        while pending and len(active) < args.max_in_flight and not stop_launching:
            current = pending.pop(0)
            item = item_by_uid[current.uid]
            try:
                staging_uri, generation, _ = storage.stage(
                    item.object_uri,
                    staging_metadata(item, current.application_job_id),
                )
                current.staging_uri = staging_uri
                current.staging_generation = generation
                current.state = "STAGED"
                persist(args.state_file, submissions)
                created = submit(
                    session, args, build_job(args, item, current), current.batch_job_id
                )
                current.batch_job_name = created["name"]
                current.state = created.get("status", {}).get("state", "QUEUED")
                active.append(current)
                print(f"submitted {current.batch_job_id} uid={current.uid}", flush=True)
            except Exception as exc:
                current.state = "SUBMISSION_FAILED"
                current.failure = str(exc)[:500]
                stop_launching = True
                print(f"submission failed {current.batch_job_id}", flush=True)
            persist(args.state_file, submissions)

        if not active:
            break
        time.sleep(args.poll_seconds)
        for current in list(active):
            try:
                job = get_job(session, current.batch_job_name)
                state = job.get("status", {}).get("state", "UNKNOWN")
                blocker = infrastructure_blocker(job)
                if blocker:
                    delete_job(session, current.batch_job_name, blocker)
                    current.state = "INFRASTRUCTURE_BLOCKED"
                    current.failure = blocker
                    active.remove(current)
                    stop_launching = True
                    print(
                        f"infrastructure blocked {current.batch_job_id}: {blocker}",
                        flush=True,
                    )
                    continue
                if state != current.state:
                    current.state = state
                    print(f"state {current.batch_job_id}={state}", flush=True)
                if state in TERMINAL_STATES:
                    active.remove(current)
                    if state != "SUCCEEDED":
                        current.failure = sanitize_failure(job)
                        stop_launching = True
            except Exception as exc:
                current.failure = f"monitoring error: {str(exc)[:400]}"
                stop_launching = True
            persist(args.state_file, submissions)

    if stop_launching:
        for item in pending:
            item.state = "NOT_SUBMITTED"
        persist(args.state_file, submissions)
    succeeded = sum(item.state == "SUCCEEDED" for item in submissions)
    print(f"controlled batch complete succeeded={succeeded} total={len(submissions)}")
    if succeeded != len(submissions):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
