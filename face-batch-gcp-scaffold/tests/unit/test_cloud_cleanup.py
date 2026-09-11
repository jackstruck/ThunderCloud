import json
from unittest.mock import Mock

import pytest

from maintenance.cloud_cleanup import cleanup


def response(data, code=200):
    return Mock(status_code=code, json=Mock(return_value=data))


def setup(tmp_path):
    job_name = "projects/p/locations/r/jobs/maintenance"
    execution_name = job_name + "/executions/attempt"
    record = {
        "stage": "submitted",
        "job": job_name,
        "job_uid": "job-uid",
        "job_spec": {"name": job_name},
        "execution": execution_name,
        "execution_uid": "execution-uid",
        "control_prefix": "gs://private/control",
        "control_generation": 1,
        "retention_deadline": "2026-10-09",
    }
    path = tmp_path / "resources.json"
    path.write_text(json.dumps(record))
    job = {"name": job_name, "uid": "job-uid", "etag": "etag"}
    execution = {
        "name": execution_name,
        "uid": "execution-uid",
        "completionTime": "2026-09-09T00:00:00Z",
    }
    api = Mock()
    api.get.side_effect = [
        response(job),
        response(execution),
        response({"executions": [execution]}),
    ]
    api.delete.return_value = response({"name": "operations/delete"})
    control = Mock(generations={})
    control.write.return_value = {"generation": 2}
    return path, api, control, job, execution


def test_cleanup_never_deletes_before_export(tmp_path):
    path, api, control, _, _ = setup(tmp_path)
    exporter = Mock(side_effect=OSError("export unavailable"))
    with pytest.raises(OSError):
        cleanup(path, tmp_path / "exports", api=api, control=control, exporter=exporter)
    api.delete.assert_not_called()
    assert json.loads(path.read_text())["stage"] == "cleanup-review"


def test_cleanup_uses_etag_and_verifies_disappearance(tmp_path):
    path, api, control, _, _ = setup(tmp_path)
    exporter = Mock(return_value={"objects": [{"sha256": "verified"}]})
    result = cleanup(
        path, tmp_path / "exports", api=api, control=control, exporter=exporter
    )
    assert result["stage"] == "cleanup-requested"
    assert api.delete.call_args.kwargs["params"] == {"etag": "etag"}
    api.get.side_effect = [response({}, 404)]
    assert (
        cleanup(
            path, tmp_path / "exports", api=api, control=control, exporter=exporter
        )["stage"]
        == "cleaned"
    )
    assert api.delete.call_count == 1 and exporter.call_count == 1
    final = json.loads(path.read_text())
    control_export = final["cleanup_control_export"]
    assert control_export["generation"] == final["control_generation"]
    assert control_export["uri"] == "gs://private/control/resources.json"
    assert (
        tmp_path / "cleanup-control.json"
    ).read_bytes() == control.write.call_args.args[1]


def test_running_execution_cannot_be_cleaned(tmp_path):
    path, api, control, job, execution = setup(tmp_path)
    execution.pop("completionTime")
    api.get.side_effect = [response(job), response(execution)]
    with pytest.raises(ValueError, match="not terminal"):
        cleanup(path, tmp_path / "exports", api=api, control=control)
    api.delete.assert_not_called()


def test_created_job_without_execution_can_be_cleaned(tmp_path):
    path, api, control, job, _ = setup(tmp_path)
    record = json.loads(path.read_text())
    record["stage"] = "create-requested"
    record.pop("execution")
    record.pop("execution_uid")
    path.write_text(json.dumps(record))
    api.get.side_effect = [response(job), response({"executions": []})]
    exporter = Mock(return_value={"objects": []})
    result = cleanup(
        path, tmp_path / "exports", api=api, control=control, exporter=exporter
    )
    assert result["stage"] == "cleanup-requested"
    assert exporter.call_args.args[2]["status"] == "not-submitted"
    api.delete.assert_called_once()
