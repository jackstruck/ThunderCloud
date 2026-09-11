import json
from copy import deepcopy
from unittest.mock import Mock

import pytest

from maintenance.cloud_launch import advance, prepare, restore


class Store:
    def __init__(self):
        self.generations = {}
        self.remote_generation = 0
        self.data = None

    def write(self, name, data):
        assert self.generations[name] == self.remote_generation, (
            "stale launcher generation"
        )
        self.remote_generation += 1
        self.generations[name] = self.remote_generation
        self.data = data
        return {"generation": self.remote_generation}


def setup(tmp_path):
    config = {
        "project": "project",
        "region": "region",
        "job_id": "maintenance-fixture",
        "image": "registry/image@sha256:" + "a" * 64,
        "service_account": "worker@project.iam.gserviceaccount.com",
        "network": "network",
        "subnetwork": "subnetwork",
        "encryption_key": "kms-key",
        "cpu": "1",
        "memory": "2Gi",
        "timeout_seconds": 3600,
        "retention_deadline": "2026-10-09",
        "owner": "implementation",
    }
    request = {
        "execution_id": "fixture",
        "image_digest": "sha256:" + "a" * 64,
        "output_prefix": "gs://private/fixture",
    }
    reference = {
        "uri": "gs://private/request",
        "generation": 7,
        "sha256": "b" * 64,
        "bytes": 123,
    }
    record = prepare(config, request, reference, tmp_path)
    return record, tmp_path / "resources.json"


def response(data, code=200):
    return Mock(status_code=code, json=Mock(return_value=data))


def test_lost_run_acknowledgement_is_reconciled_without_resubmission(tmp_path):
    record, path = setup(tmp_path)
    observed = {**deepcopy(record["job_spec"]), "uid": "job-uid", "etag": "etag"}
    execution = {"name": record["job"] + "/executions/attempt", "uid": "execution-uid"}
    api = Mock()
    store = Store()
    api.get.side_effect = [response({}, 404), response(observed)]
    api.post.side_effect = [
        response({"name": "operations/create"}),
        TimeoutError("lost acknowledgement"),
    ]
    with pytest.raises(TimeoutError):
        advance(path, api=api, store=store)
    assert api.post.call_count == 2
    assert "name" not in api.post.call_args_list[0].kwargs["json"]
    api.get.side_effect = [response(observed), response({"executions": [execution]})]
    result = advance(path, api=api, store=store)
    assert result["execution_uid"] == "execution-uid" and result["stage"] == "submitted"
    assert api.post.call_count == 2
    task = record["job_spec"]["template"]["template"]
    assert task["maxRetries"] == 0
    assert task["vpcAccess"]["egress"] == "PRIVATE_RANGES_ONLY"
    assert task["containers"][0]["command"] == [
        "python",
        "-m",
        "maintenance.cloud_runner",
    ]


def test_existing_job_is_not_reused(tmp_path):
    _, path = setup(tmp_path)
    api = Mock()
    store = Store()
    api.get.return_value = response({"uid": "someone-elses-job"})
    with pytest.raises(ValueError, match="already exists"):
        advance(path, api=api, store=store)
    api.post.assert_not_called()


def test_stale_launcher_cannot_submit_and_remote_state_restores(tmp_path):
    record, path = setup(tmp_path / "first")
    stale = tmp_path / "stale.json"
    stale.write_bytes(path.read_bytes())
    store = Store()
    api = Mock()
    api.get.side_effect = [response({}, 404), response({}, 404)]
    api.post.return_value = response({"name": "operation/create"})
    advance(path, api=api, store=store)
    with pytest.raises(AssertionError, match="stale launcher generation"):
        advance(stale, api=api, store=store)
    assert api.post.call_count == 1
    client = Mock()
    blob = client.bucket.return_value.get_blob.return_value
    blob.generation = store.remote_generation
    blob.download_as_bytes.return_value = store.data
    restored = restore(record["control_prefix"], tmp_path / "restored", client=client)
    assert restored["stage"] == "create-requested"
    assert restored["control_generation"] == store.remote_generation
    assert (
        json.loads((tmp_path / "restored" / "resources.json").read_text()) == restored
    )
