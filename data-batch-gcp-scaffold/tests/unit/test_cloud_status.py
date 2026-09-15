from copy import deepcopy
import hashlib
import json

import pytest

from maintenance.cloud import classify


def fixture():
    manifest = {
        "execution": "projects/p/locations/r/jobs/j/executions/e",
        "execution_uid": "uid",
        "execution_id": "reviewed",
        "image": "registry/image@sha256:abc",
    }
    execution = {
        "name": manifest["execution"],
        "uid": "uid",
        "template": {"containers": [{"image": manifest["image"]}]},
        "completionTime": "2026-09-09T00:00:00Z",
        "succeededCount": 1,
        "taskCount": 1,
    }
    report = {
        "execution_id": "reviewed",
        "cloud_run_execution": "e",
        "image_digest": "sha256:abc",
        "status": "succeeded",
        "remaining": [],
    }
    return manifest, execution, report


def test_completion_requires_execution_and_matching_final_report():
    manifest, execution, report = fixture()
    assert classify(execution, report, None, manifest)["status"] == "succeeded"
    assert (
        classify(execution, None, report, manifest)["status"]
        == "interrupted/incomplete"
    )
    assert (
        classify(None, report, report, manifest)["status"] == "interrupted/incomplete"
    )
    stale = {**report, "cloud_run_execution": "previous-attempt"}
    assert (
        classify(execution, stale, None, manifest)["status"] == "interrupted/incomplete"
    )
    active = deepcopy(execution)
    active.pop("completionTime")
    assert classify(active, report, report, manifest)["status"] == "running"
    assert (
        classify({**execution, "failedCount": 1}, report, report, manifest)["status"]
        == "failed"
    )


def test_status_refuses_replaced_execution():
    manifest, execution, report = fixture()
    with pytest.raises(ValueError, match="identity"):
        classify({**execution, "uid": "replacement"}, report, None, manifest)


def test_success_can_still_require_resource_cleanup():
    manifest, execution, report = fixture()
    report["remaining"] = ["Export report checksums and run scoped cleanup"]
    result = classify(execution, report, None, manifest)
    assert result["status"] == "succeeded"
    assert result["remaining"] == report["remaining"]


def test_execution_platform_digest_requires_verified_index():
    manifest, execution, report = fixture()
    child = "sha256:" + "b" * 64
    raw = json.dumps(
        {
            "manifests": [
                {"digest": child, "platform": {"architecture": "amd64", "os": "linux"}}
            ]
        }
    )
    parent = "sha256:" + hashlib.sha256(raw.encode()).hexdigest()
    manifest.update(image="registry/image@" + parent, image_index=raw)
    report["image_digest"] = parent
    execution["template"]["containers"][0]["image"] = "registry/image@" + child
    assert classify(execution, report, None, manifest)["status"] == "succeeded"
    with pytest.raises(ValueError, match="index checksum"):
        classify(execution, report, None, {**manifest, "image_index": raw + " "})
    execution["template"]["containers"][0]["image"] = (
        "registry/image@sha256:" + "c" * 64
    )
    with pytest.raises(ValueError, match="image differs"):
        classify(execution, report, None, manifest)
