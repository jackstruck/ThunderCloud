from unittest.mock import patch

import pytest

from worker.config import Settings
from worker.process import run


def settings(tmp_path):
    key = tmp_path / "key"
    key.write_text("unused", encoding="ascii")
    return Settings(
        "p", "bucket", "videos/", "face-staging/", key, "instance", "user@example.com"
    )


@patch("worker.process.process_video", return_value=[])
@patch("worker.process.OnnxFaceEmbedder")
@patch("worker.process.ByteTrackTracker")
@patch("worker.process.ScrfdDetector")
@patch("worker.process.Database")
@patch("worker.process.StorageRepository")
@patch("worker.process.load_csek", return_value=b"x" * 32)
def test_delete_occurs_only_after_commit(
    load_key,
    storage_class,
    database_class,
    detector,
    tracker,
    embedder,
    process_video_mock,
    tmp_path,
):
    storage = storage_class.return_value
    storage.download_staging.return_value = (
        b"video",
        "0cab1c9617404faf2b8cf28a8210988d58bdf5f11e3e0c9b4bcca8e0b29629c0",
    )
    database = database_class.return_value
    database.commit_results.return_value = True
    run(
        gcs_uri="gs://bucket/face-staging/x/video.mp4",
        external_source_ref="gs://bucket/videos/video.mp4",
        expected_sha256=None,
        job_id="4a0e791d-fc5e-44ae-922f-9b71d91dca90",
        settings=settings(tmp_path),
    )
    assert database.commit_results.call_count == 1
    assert storage.delete_staging.call_count == 1


@patch("worker.process.process_video", return_value=[])
@patch("worker.process.OnnxFaceEmbedder")
@patch("worker.process.ByteTrackTracker")
@patch("worker.process.ScrfdDetector")
@patch("worker.process.Database")
@patch("worker.process.StorageRepository")
@patch("worker.process.load_csek", return_value=b"x" * 32)
def test_failed_commit_leaves_staging(
    load_key,
    storage_class,
    database_class,
    detector,
    tracker,
    embedder,
    process_video_mock,
    tmp_path,
):
    storage = storage_class.return_value
    storage.download_staging.return_value = (
        b"video",
        "0cab1c9617404faf2b8cf28a8210988d58bdf5f11e3e0c9b4bcca8e0b29629c0",
    )
    database_class.return_value.commit_results.side_effect = RuntimeError("db failed")
    with pytest.raises(RuntimeError, match="db failed"):
        run(
            gcs_uri="gs://bucket/face-staging/x/video.mp4",
            external_source_ref="gs://bucket/videos/video.mp4",
            expected_sha256=None,
            job_id="4a0e791d-fc5e-44ae-922f-9b71d91dca90",
            settings=settings(tmp_path),
        )
    storage.delete_staging.assert_not_called()


@patch("worker.process.Database")
@patch("worker.process.StorageRepository")
@patch("worker.process.load_csek", return_value=b"x" * 32)
def test_hash_mismatch_aborts_before_database_or_delete(
    load_key, storage_class, database_class, tmp_path
):
    storage = storage_class.return_value
    storage.download_staging.return_value = (b"video", "b" * 64)
    with pytest.raises(ValueError, match="does not match"):
        run(
            gcs_uri="gs://bucket/face-staging/x/video.mp4",
            external_source_ref="gs://bucket/videos/video.mp4",
            expected_sha256="a" * 64,
            job_id="4a0e791d-fc5e-44ae-922f-9b71d91dca90",
            settings=settings(tmp_path),
        )
    database_class.assert_not_called()
    storage.delete_staging.assert_not_called()
