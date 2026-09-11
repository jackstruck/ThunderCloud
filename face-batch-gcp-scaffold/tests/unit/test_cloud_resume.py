import json
from unittest.mock import Mock

import pytest

from maintenance.cloud_resume import prepare, require_terminal


def fixture():
    image = "sha256:" + "a" * 64
    previous = {
        "execution": "projects/p/locations/r/jobs/j/executions/e",
        "execution_uid": "uid",
        "execution_id": "fixture",
        "image": "registry/image@" + image,
        "output_prefix": "gs://private/fixture",
    }
    execution = {
        "name": previous["execution"],
        "uid": "uid",
        "completionTime": "2026-09-09T00:00:00Z",
        "failedCount": 1,
        "taskCount": 1,
        "template": {"containers": [{"image": previous["image"]}]},
    }
    original = {
        "format": 1,
        "execution_id": "fixture",
        "image_digest": image,
        "output_prefix": previous["output_prefix"],
        "inputs": {"plan": {"generation": 7}},
        "plan_checksum": "reviewed",
    }
    progress = {
        "execution_id": "fixture",
        "cloud_run_execution": "e",
        "image_digest": image,
        "status": "failed",
        "attempt": 1,
        "last_committed_checkpoint": {"version": 12},
    }
    api = Mock()
    api.get.return_value = Mock(status_code=200, json=Mock(return_value=execution))
    return previous, execution, original, progress, api


def test_running_or_missing_execution_does_not_authorize_resume():
    previous, execution, _, _, api = fixture()
    reference = {"name": previous["execution"], "uid": "uid"}
    execution.pop("completionTime")
    with pytest.raises(ValueError, match="not terminal"):
        require_terminal(reference, api)
    api.get.return_value.status_code = 404
    with pytest.raises(ValueError, match="missing or replaced"):
        require_terminal(reference, api)


def test_resume_pins_progress_and_preserves_reviewed_inputs(tmp_path):
    previous, _, original, progress, api = fixture()
    raw = json.dumps(progress).encode()
    blob = Mock(generation=19, download_as_bytes=Mock(return_value=raw))
    client = Mock()
    client.bucket.return_value.get_blob.side_effect = [None, blob]
    resumed = prepare(previous, original, tmp_path, api=api, client=client)
    assert resumed["inputs"] == original["inputs"]
    assert resumed["plan_checksum"] == original["plan_checksum"]
    assert resumed["resume_progress"]["generation"] == 19
    assert resumed["resume_from"]["uid"] == "uid"
    assert "resume_from" not in original
    blob.download_as_bytes.assert_called_once_with(if_generation_match=19, timeout=30)
