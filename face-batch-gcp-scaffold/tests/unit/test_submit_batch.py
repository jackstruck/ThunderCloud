import importlib.util
import sys
from argparse import Namespace
from pathlib import Path
from types import ModuleType

from worker.manifest import ManifestItem

google = ModuleType("google")
google_auth = ModuleType("google.auth")
google_transport = ModuleType("google.auth.transport")
google_requests = ModuleType("google.auth.transport.requests")
google_requests.AuthorizedSession = object
google.auth = google_auth
sys.modules.setdefault("google", google)
sys.modules.setdefault("google.auth", google_auth)
sys.modules.setdefault("google.auth.transport", google_transport)
sys.modules.setdefault("google.auth.transport.requests", google_requests)

SPEC = importlib.util.spec_from_file_location(
    "submit_batch", Path(__file__).parents[2] / "scripts" / "submit_batch.py"
)
submit_batch = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = submit_batch
SPEC.loader.exec_module(submit_batch)


def item(uid: str) -> ManifestItem:
    return ManifestItem(
        uid,
        f"gs://bucket/videos/{uid}.mp4",
        "a" * 64,
        1,
        "video/mp4",
        None,
        None,
        {},
    )


def test_controlled_selection_excludes_prior_test():
    selected = submit_batch.select_controlled(
        [item(str(index)) for index in range(4)], 2, {"0"}
    )
    assert [record.uid for record in selected] == ["1", "2"]


def test_job_requires_matching_private_sql_and_cuda_preflight():
    args = Namespace(
        image="us-central1-docker.pkg.dev/p/repo/worker@sha256:" + "a" * 64,
        region="us-central1",
        project="p",
        bucket="bucket",
        source_prefix="videos/",
        staging_prefix="face-staging/",
        service_account="worker@p.iam.gserviceaccount.com",
        network="network",
        subnetwork="subnet",
        machine_type="g2-standard-8",
        on_demand=False,
        max_run_seconds=100,
    )
    record = item("uid")
    submission = submit_batch.Submission(
        "uid",
        record.object_uri,
        record.sha256,
        "app",
        "batch",
        staging_uri="gs://bucket/face-staging/a",
        staging_generation=1,
    )
    job = submit_batch.build_job(args, record, submission)
    task = job["taskGroups"][0]["taskSpec"]
    variables = task["environment"]["variables"]
    assert variables["FACE_MATCHING_ENABLED"] == "true"
    assert variables["FACE_CLOUD_SQL_IP_TYPE"] == "PRIVATE"
    assert variables["FACE_CSEK_SECRET"] == "face-batch-gcs-csek"
    assert task["runnables"][0]["container"]["commands"] == [
        "/app/scripts/verify_gpu_image.py"
    ]


def test_gpu_quota_event_stops_rollout():
    job = {
        "status": {
            "statusEvents": [
                {"description": "Batch Error: code - CODE_GCE_QUOTA_EXCEEDED"}
            ]
        }
    }
    assert submit_batch.infrastructure_blocker(job) == "GPU quota exceeded"
