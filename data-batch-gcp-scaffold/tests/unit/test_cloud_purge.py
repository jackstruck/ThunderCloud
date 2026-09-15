import hashlib
import json
from datetime import UTC, datetime
from unittest.mock import Mock

import pytest

from maintenance.cloud_purge import purge


def test_disposal_waits_for_deadline_and_pins_exported_generation(tmp_path):
    artifact = tmp_path / "report.json"
    artifact.write_bytes(b"exported")
    resources = tmp_path / "resources.json"
    resources.write_text(
        json.dumps(
            {
                "stage": "cleaned",
                "job": "owned-job",
                "output_prefix": "gs://private/execution",
                "cleanup_export": {
                    "retained": {"deadline": "2026-10-09", "owner": "implementation"},
                    "objects": [
                        {
                            "uri": "gs://private/execution/report.json",
                            "generation": 7,
                            "local": str(artifact),
                            "bytes": 8,
                            "sha256": hashlib.sha256(b"exported").hexdigest(),
                        }
                    ],
                },
            }
        )
    )
    client = Mock()
    with pytest.raises(ValueError, match="not expired"):
        purge(
            resources,
            tmp_path / "purge.json",
            client=client,
            now=datetime(2026, 10, 9, tzinfo=UTC),
        )
    client.bucket.assert_not_called()
    result = purge(
        resources,
        tmp_path / "purge.json",
        client=client,
        now=datetime(2026, 10, 10, tzinfo=UTC),
    )
    assert result["removed"][0]["generation"] == 7
    client.bucket.return_value.blob.assert_called_once_with(
        "execution/report.json", generation=7
    )
    client.bucket.return_value.blob.return_value.delete.assert_called_once_with(
        if_generation_match=7, timeout=30
    )
    artifact.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="export changed"):
        purge(
            resources,
            tmp_path / "purge.json",
            client=client,
            now=datetime(2026, 10, 10, tzinfo=UTC),
        )
    assert client.bucket.call_count == 1


def test_purge_includes_control_generation_written_after_job_cleanup(tmp_path):
    control = tmp_path / "cleanup-control.json"
    control.write_bytes(b"final-control")
    resources = tmp_path / "resources.json"
    record = {
        "stage": "cleaned",
        "job": "owned-job",
        "output_prefix": "gs://private/execution",
        "control_prefix": "gs://private/execution/control/job",
        "control_generation": 12,
        "cleanup_export": {
            "retained": {"deadline": "2026-10-09", "owner": "implementation"},
            "objects": [],
        },
        "cleanup_control_export": {
            "uri": "gs://private/execution/control/job/resources.json",
            "generation": 12,
            "local": str(control),
            "bytes": len(control.read_bytes()),
            "sha256": hashlib.sha256(control.read_bytes()).hexdigest(),
        },
    }
    resources.write_text(json.dumps(record))
    client = Mock()
    result = purge(
        resources,
        tmp_path / "purge.json",
        client=client,
        now=datetime(2026, 10, 10, tzinfo=UTC),
    )
    assert result["removed"] == [
        {"uri": record["cleanup_control_export"]["uri"], "generation": 12}
    ]
    client.bucket.return_value.blob.assert_called_once_with(
        "execution/control/job/resources.json", generation=12
    )
    record["cleanup_control_export"]["generation"] = 11
    resources.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="Final cleanup control generation"):
        purge(
            resources,
            tmp_path / "purge.json",
            client=client,
            now=datetime(2026, 10, 10, tzinfo=UTC),
        )
