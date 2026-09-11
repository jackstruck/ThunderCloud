import json
from unittest.mock import Mock

import pytest

from maintenance.durable import CloudArtifacts


class Bucket:
    def __init__(self):
        self.objects = {}
        self.calls = []
        self.failure = None

    def get_blob(self, key):
        return Mock(generation=self.objects[key][0]) if key in self.objects else None

    def blob(self, key):
        bucket = self

        class Blob:
            def upload_from_string(self, data, *, if_generation_match, **kwargs):
                assert if_generation_match == bucket.objects.get(key, (0,))[0], (
                    "generation conflict"
                )
                if key == bucket.failure:
                    raise OSError("unavailable")
                self.generation = if_generation_match + 1
                bucket.objects[key] = (self.generation, data)
                bucket.calls.append(key)

        return Blob()


def store(bucket, resume=False):
    return CloudArtifacts(
        "gs://private/maintenance/execution",
        client=Mock(bucket=Mock(return_value=bucket)),
        resume=resume,
    )


def test_progress_does_not_overwrite_another_writer():
    bucket = Bucket()
    first, second = store(bucket), store(bucket)
    first.progress({"stage": "one"})
    with pytest.raises(AssertionError, match="generation conflict"):
        second.progress({"stage": "two"})
    resumed = store(bucket, resume=True)
    resumed.progress({"stage": "resumed"})
    with pytest.raises(AssertionError, match="generation conflict"):
        first.progress({"stage": "stale"})
    assert (
        json.loads(bucket.objects["maintenance/execution/progress.json"][1])["stage"]
        == "resumed"
    )


def test_final_report_waits_for_artifact_export(tmp_path):
    bucket = Bucket()
    output = store(bucket)
    (tmp_path / "before.json").write_text('{"hash":"fixture"}')
    (tmp_path / "report.json").write_text('{"status":"succeeded"}')
    bucket.failure = "maintenance/execution/before.json"
    with pytest.raises(OSError):
        output.finish(tmp_path)
    assert "maintenance/execution/report.json" not in bucket.objects
    bucket.failure = None
    output.finish(tmp_path)
    assert bucket.calls[-1] == "maintenance/execution/report.json"
    manifest = json.loads(
        bucket.objects["maintenance/execution/artifact-manifest.json"][1]
    )
    assert manifest["artifacts"][0]["generation"] == 1
    assert len(manifest["artifacts"][0]["sha256"]) == 64
