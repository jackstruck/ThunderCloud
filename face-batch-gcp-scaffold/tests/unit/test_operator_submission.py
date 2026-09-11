import hashlib
from types import SimpleNamespace

import pytest
from google.api_core.exceptions import PreconditionFailed

from worker.operator_submission import prepare, submit
from worker.storage import StorageRepository


def test_archive_retry_reuses_only_verified_temporary_output():
    data = b"archive bytes"
    work = SimpleNamespace(
        run_id="00000000-0000-0000-0000-000000000001",
        archive_bucket="archive",
        archive_object_name="sources/video.mp4",
        archive_generation=7,
        archive_sha256=hashlib.sha256(data).hexdigest(),
        expected_bytes=len(data),
        content_type="video/mp4",
    )
    storage = object.__new__(StorageRepository)
    storage.source_uri = lambda uri: uri
    storage.download_source_generation = lambda *args: data
    storage.upload_temporary = lambda *args: (_ for _ in ()).throw(
        PreconditionFailed("already acquired")
    )
    storage.temporary_generation = lambda name: 12
    storage.download_temporary = lambda *args: (data, work.archive_sha256)
    assert storage.archive_to_temporary(work, 100)[1:] == (12, len(data))
    storage.download_temporary = lambda *args: (b"changed", "0" * 64)
    with pytest.raises(ValueError, match="Prior acquisition output differs"):
        storage.archive_to_temporary(work, 100)
    work.archive_sha256 = "0" * 64
    with pytest.raises(ValueError, match="checksum differs"):
        storage.archive_to_temporary(work, 100)


def test_operator_requires_explicit_policies_and_refuses_changed_receipt(tmp_path):
    import json

    payload = {
        "handling_policy": "enroll_only",
        "selection_policy": "all_tracks",
        "source": {
            "kind": "archive",
            "bucket": "archive",
            "object_name": "sources/file.mp4",
            "generation": 1,
            "sha256": "a" * 64,
            "bytes": 20,
            "content_type": "video/mp4",
            "page_url": None,
        },
    }
    path = tmp_path / "receipt.json"
    with pytest.raises(ValueError, match="selection"):
        prepare(path, "operator", {**payload, "selection_policy": "automatic-guessed"})
    prepare(path, "operator", payload)
    assert path.stat().st_mode & 0o777 == 0o600
    receipt = json.loads(path.read_text())
    receipt["payload"]["source"]["generation"] = 2
    path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match="receipt changed"):
        submit(path, object())
