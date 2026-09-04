from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import backfill_inventory, backfill_stage1
from .config import Settings
from .storage import GcsUri, has_customer_encryption, load_configured_csek

AUDIT_PREFIX = "backfill-audit/"


def _storage(settings: Settings):
    from google.cloud import storage

    csek = load_configured_csek(
        settings.project_id, settings.csek_file, settings.csek_secret
    )
    client = storage.Client(project=settings.project_id)
    return client.bucket(settings.bucket), csek


def _upload_file(bucket, csek: bytes, path: Path, object_name: str) -> str:
    blob = bucket.blob(object_name, encryption_key=csek)
    if blob.exists():
        blob.reload()
        if not has_customer_encryption(blob):
            raise RuntimeError("existing audit object is not CSEK encrypted")
        return f"gs://{bucket.name}/{object_name}"
    blob.upload_from_filename(str(path), if_generation_match=0)
    blob.reload()
    if not has_customer_encryption(blob):
        raise RuntimeError("audit upload does not report customer-supplied encryption")
    return f"gs://{bucket.name}/{object_name}"


def _download_file(bucket, csek: bytes, uri: str, destination: Path) -> None:
    parsed = GcsUri.parse(uri)
    if parsed.bucket != bucket.name or not parsed.object_name.startswith(AUDIT_PREFIX):
        raise ValueError("audit input must use the configured bucket and audit prefix")
    blob = bucket.blob(parsed.object_name, encryption_key=csek)
    blob.reload()
    if not has_customer_encryption(blob):
        raise RuntimeError("audit input does not report customer-supplied encryption")
    blob.download_to_filename(str(destination))


def _gcloud() -> tuple[str, dict[str, str]]:
    binary = os.getenv("FACE_GCLOUD_BIN") or shutil.which("gcloud")
    fallback = "/workspaces/ThunderCloud/google-cloud-sdk/bin/gcloud"
    if binary is None and os.path.isfile(fallback):
        binary = fallback
    if binary is None:
        raise RuntimeError("gcloud is not installed")
    import google.auth
    from google.auth.transport.requests import Request

    credentials, _ = google.auth.default()
    credentials.refresh(Request())
    environment = os.environ.copy()
    environment["CLOUDSDK_AUTH_ACCESS_TOKEN"] = credentials.token
    return binary, environment


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def launch(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    settings.validate()
    bucket, csek = _storage(settings)
    inputs = {}
    for label, path in {
        "manifest": args.manifest,
        "justpaste_input": args.justpaste_input,
        "justpaste_checkpoint": args.justpaste_checkpoint,
    }.items():
        digest = _digest(path)
        inputs[label] = _upload_file(
            bucket, csek, path, f"{AUDIT_PREFIX}inputs/{digest}-{path.name}"
        )
    values = {
        "BACKFILL_MANIFEST_URI": inputs["manifest"],
        "BACKFILL_JUSTPASTE_INPUT_URI": inputs["justpaste_input"],
        "BACKFILL_JUSTPASTE_CHECKPOINT_URI": inputs["justpaste_checkpoint"],
        "BACKFILL_WORKER_VERSION": args.worker_version,
        "BACKFILL_DETECTOR_VERSION": args.detector_version,
        "BACKFILL_EMBEDDING_MODEL_VERSION": args.embedding_model_version,
        "BACKFILL_THRESHOLD_VERSION": args.threshold_version,
    }
    gcloud, environment = _gcloud()
    command = [
        gcloud,
        "run",
        "jobs",
        "execute",
        args.job,
        f"--project={args.project}",
        f"--region={args.region}",
        "--tasks=1",
        "--update-env-vars=" + ",".join(f"{k}={v}" for k, v in values.items()),
        "--async",
        "--format=json",
    ]
    completed = subprocess.run(
        command, check=True, capture_output=True, text=True, env=environment
    )
    execution = json.loads(completed.stdout)
    print(
        json.dumps(
            {
                "execution_name": execution["metadata"]["name"],
                "job": args.job,
                "inputs": inputs,
                "result_prefix": f"gs://{bucket.name}/{AUDIT_PREFIX}results/",
            }
        )
    )
    return 0


def remote_run() -> int:
    settings = Settings.from_env()
    settings.validate()
    bucket, csek = _storage(settings)
    if os.getenv("BACKFILL_REMOTE_STAGE") == "stage1-reconcile":
        with tempfile.TemporaryDirectory(prefix="face-backfill-stage1-") as directory:
            inventory = Path(directory) / "inventory.json"
            _download_file(
                bucket, csek, os.environ["BACKFILL_INVENTORY_URI"], inventory
            )
            frozen = backfill_stage1.load_inventory(
                inventory, os.environ.get("BACKFILL_INVENTORY_SHA256")
            )
            database = backfill_stage1.Database(
                settings.cloud_sql_instance,
                settings.db_user,
                settings.db_name,
                settings.cloud_sql_ip_type,
            )
            try:
                report = backfill_stage1.reconcile(
                    frozen, backfill_stage1.Stage1Repository(database)
                )
            finally:
                database.close()
            print(json.dumps({"stage": "stage1-reconcile", **report}, sort_keys=True))
            return 2 if report.get("blocked") else 0
    if os.getenv("BACKFILL_REMOTE_STAGE") == "stage1-refresh-attribution":
        database = backfill_stage1.Database(
            settings.cloud_sql_instance,
            settings.db_user,
            settings.db_name,
            settings.cloud_sql_ip_type,
        )
        try:
            updated = backfill_stage1.Stage1Repository(database).refresh_attribution()
        finally:
            database.close()
        print(
            json.dumps(
                {
                    "stage": "stage1-refresh-attribution",
                    "candidate_rows_updated": updated,
                },
                sort_keys=True,
            )
        )
        return 0
    required = {
        "manifest": "BACKFILL_MANIFEST_URI",
        "justpaste-input": "BACKFILL_JUSTPASTE_INPUT_URI",
        "justpaste-checkpoint": "BACKFILL_JUSTPASTE_CHECKPOINT_URI",
    }
    with tempfile.TemporaryDirectory(prefix="face-backfill-") as directory:
        root = Path(directory)
        argv = ["--output-dir", str(root / "results")]
        for option, variable in required.items():
            destination = root / option
            _download_file(bucket, csek, os.environ[variable], destination)
            argv.extend([f"--{option}", str(destination)])
        for option, variable in {
            "worker-version": "BACKFILL_WORKER_VERSION",
            "detector-version": "BACKFILL_DETECTOR_VERSION",
            "embedding-model-version": "BACKFILL_EMBEDDING_MODEL_VERSION",
            "threshold-version": "BACKFILL_THRESHOLD_VERSION",
        }.items():
            argv.extend([f"--{option}", os.environ[variable]])
        code, outputs = backfill_inventory.run(
            backfill_inventory.parser().parse_args(argv)
        )
        uploaded = [
            _upload_file(bucket, csek, path, f"{AUDIT_PREFIX}results/{path.name}")
            for path in outputs
        ]
        print(json.dumps({"exit_code": code, "outputs": uploaded}))
        # A blocked classification is a completed audit requiring review, not an
        # infrastructure failure. Preserve its acceptance code in the result log.
        return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Launch the Stage 0 inventory on Cloud Run"
    )
    result.add_argument(
        "--manifest", type=Path, default=Path("../bulk-download/data/manifest.jsonl")
    )
    result.add_argument(
        "--justpaste-input",
        type=Path,
        default=Path("../bulk-download/input/justpaste_urls.txt"),
    )
    result.add_argument(
        "--justpaste-checkpoint",
        type=Path,
        default=Path("../bulk-download/data/justpaste_resolution.jsonl"),
    )
    result.add_argument("--job", default="face-batch-backfill-inventory")
    result.add_argument("--project", default="teak-banner-dome")
    result.add_argument("--region", default="us-central1")
    result.add_argument("--worker-version", required=True)
    result.add_argument("--detector-version", required=True)
    result.add_argument("--embedding-model-version", required=True)
    result.add_argument("--threshold-version", required=True)
    return result


def main(argv: list[str] | None = None) -> int:
    return launch(parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(
        remote_run() if os.getenv("BACKFILL_REMOTE_RUN") == "true" else main()
    )
