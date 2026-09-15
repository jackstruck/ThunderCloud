"""Cloud maintenance control using execution state and durable report evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

from maintenance.durable import CloudArtifacts

API = "https://run.googleapis.com/v2/"


def execution_image(manifest):
    """Resolve an OCI index only through its checksum-bound platform descriptor."""
    pinned = manifest["image"]
    raw = manifest.get("image_index")
    if raw is None:
        return pinned
    if "sha256:" + hashlib.sha256(raw.encode()).hexdigest() != pinned.rsplit("@", 1)[1]:
        raise ValueError("Image index checksum differs from the reviewed image")
    candidates = [
        item["digest"]
        for item in json.loads(raw).get("manifests", [])
        if item.get("platform") == {"architecture": "amd64", "os": "linux"}
    ]
    if len(candidates) != 1 or not re.fullmatch(r"sha256:[0-9a-f]{64}", candidates[0]):
        raise ValueError("Image index must identify one Linux amd64 image")
    return pinned.rsplit("@", 1)[0] + "@" + candidates[0]


def session():
    import google.auth
    from google.auth.transport.requests import AuthorizedSession

    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    return AuthorizedSession(credentials)


def get_execution(api, name):
    if not re.fullmatch(
        r"projects/[^/]+/locations/[^/]+/jobs/[^/]+/executions/[^/]+", name
    ):
        raise ValueError("An exact Cloud Run execution name is required")
    response = api.get(API + name, timeout=30)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.json()


def read_json(bucket, prefix, name):
    blob = bucket.get_blob(prefix + "/" + name)
    if blob is None:
        return None
    return json.loads(
        blob.download_as_bytes(if_generation_match=int(blob.generation), timeout=30)
    )


def classify(execution, report, progress, manifest):
    result = {
        "execution": manifest["execution"],
        "status": "interrupted/incomplete",
        "stage": None,
        "heartbeat_at": None,
        "remaining": [],
    }
    if execution is None:
        result["remaining"] = ["Cloud Run execution is missing; completion is unproven"]
        return result
    if (
        execution.get("name") != manifest["execution"]
        or execution.get("uid") != manifest["execution_uid"]
    ):
        raise ValueError("Cloud execution identity differs from the resource manifest")
    images = [
        c.get("image") for c in execution.get("template", {}).get("containers", [])
    ]
    resolved = execution_image(manifest)
    if images not in ([manifest["image"]], [resolved]):
        raise ValueError("Cloud execution image differs from the resource manifest")

    def belongs(value):
        return value is not None and all(
            (
                value.get("execution_id") == manifest["execution_id"],
                value.get("cloud_run_execution")
                == manifest["execution"].rsplit("/", 1)[1],
                value.get("image_digest") == manifest["image"].rsplit("@", 1)[1],
            )
        )

    current = report if belongs(report) else progress if belongs(progress) else None
    if current:
        result.update(
            stage=current.get("stage"), heartbeat_at=current.get("heartbeat_at")
        )
    terminal = bool(execution.get("completionTime"))
    if not terminal:
        result["status"] = "running"
        result["remaining"] = ["Cloud Run execution has not reached a terminal state"]
        return result
    if execution.get("failedCount", 0) or execution.get("cancelledCount", 0):
        result["status"] = "failed"
        result["remaining"] = [
            "Cloud Run execution failed or was cancelled; inspect durable reports"
        ]
    elif (
        execution.get("succeededCount") == 1
        and execution.get("taskCount") == 1
        and belongs(report)
        and report.get("status") == "succeeded"
    ):
        result["status"] = "succeeded"
        result["remaining"] = report.get("remaining", [])
    elif belongs(report) and report.get("status") == "failed":
        result["status"] = "failed"
        result["remaining"] = report.get("remaining", [])
    else:
        result["remaining"] = [
            "Terminal execution lacks a matching successful final report"
        ]
    return result


def status(manifest, *, api=None, client=None):
    store = CloudArtifacts(manifest["output_prefix"], client=client)
    execution = get_execution(api or session(), manifest["execution"])
    report = read_json(store.bucket, store.prefix, "report.json")
    progress = read_json(store.bucket, store.prefix, "progress.json")
    return classify(execution, report, progress, manifest)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["status"])
    parser.add_argument("--resources", type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(status(json.loads(args.resources.read_text())), indent=2))


if __name__ == "__main__":
    main()
