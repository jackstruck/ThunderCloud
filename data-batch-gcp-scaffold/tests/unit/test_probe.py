import os
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from worker.config import Settings
from worker.db import RankedSubject
from worker.models import Candidate, Detection, TrackTemplate
from worker.probe import write_review
from worker.probe_service import (
    LocalMedia,
    ProbeRun,
    ProbeService,
    ReviewCrop,
    image_faces,
    read_local_media,
    video_faces,
)


class Detector:
    def __init__(self, detections=()):
        self.detections = detections

    def detect(self, frame):
        return list(self.detections)


class Embedder:
    def embed(self, frame, detection):
        return [512**-0.5] * 512


class Database:
    def __init__(self):
        self.calls = []

    def search_subjects(self, embeddings, model, top_k):
        self.calls.append((embeddings, model, top_k))
        observation = {
            "video_uri": "gs://bucket/videos/a.mp4",
            "source_sha256": "a" * 64,
            "track_id": "track",
            "start_ms": 1,
            "end_ms": 2,
            "max_quality": 0.9,
            "mean_quality": 0.8,
            "processing_completed_at": "now",
            "source_id": "not-returned",
            "processing_job_id": "not-returned",
        }
        return [
            [RankedSubject("subject", 0.8, "private-label", (observation,))]
            for _ in embeddings
        ]

    def close(self):
        pass


def settings(tmp_path, **overrides):
    values = {
        "project_id": "p",
        "bucket": "bucket",
        "source_prefix": "videos/",
        "staging_prefix": "face-staging/",
        "csek_file": tmp_path / "key",
        "csek_secret": None,
        "cloud_sql_instance": "i",
        "db_user": "u",
    }
    values.update(overrides)
    return Settings(**values)


def test_local_media_rejects_symlink_unsupported_and_wrong_signature(tmp_path):
    target = tmp_path / "face.jpg"
    target.write_bytes(b"\xff\xd8\xffx")
    link = tmp_path / "link.jpg"
    link.symlink_to(target)
    with pytest.raises(ValueError, match="non-symlink"):
        read_local_media(link, settings(tmp_path))
    unsupported = tmp_path / "face.gif"
    unsupported.write_bytes(b"x")
    with pytest.raises(ValueError, match="unsupported"):
        read_local_media(unsupported, settings(tmp_path))
    wrong = tmp_path / "face.jpg"
    wrong.write_bytes(b"not-jpeg")
    with pytest.raises(ValueError, match="signature"):
        read_local_media(wrong, settings(tmp_path))


def test_image_face_mapping_and_review_crop(tmp_path, monkeypatch):
    monkeypatch.setattr("worker.probe_service.decode_image", lambda *_: object())
    monkeypatch.setattr("worker.probe_service._face_crop_jpeg", lambda *_: b"crop")
    media = LocalMedia("image/jpeg", b"x", "hash")
    assert image_faces(media, settings(tmp_path), Detector(), Embedder()) == ([], [])
    faces, crops = image_faces(
        media, settings(tmp_path), Detector([Detection((1, 2, 3, 4), 0.9)]), Embedder()
    )
    assert faces[0]["bbox"] == (1, 2, 3, 4) and len(faces[0]["embedding"]) == 512
    assert crops == [ReviewCrop("face-000000/review.jpg", b"crop")]


def test_video_template_mapping_and_duration_limit(tmp_path, monkeypatch):
    embedding = [512**-0.5] * 512
    template = TrackTemplate(
        7, 100, 900, 4, [Candidate(0.8, 100, embedding, crop_jpeg=b"crop")], embedding
    )
    monkeypatch.setattr("worker.probe_service.ByteTrackTracker", lambda *_: object())
    monkeypatch.setattr("worker.probe_service.process_video", lambda *args: [template])
    monkeypatch.setattr(
        "av.open",
        lambda *args, **kwargs: nullcontext(SimpleNamespace(duration=1_000_000)),
    )
    faces, crops = video_faces(
        LocalMedia("video/mp4", b"video", "hash"),
        settings(tmp_path),
        Detector(),
        Embedder(),
    )
    assert faces[0]["local_face_id"] == 7
    assert crops == [ReviewCrop("track-000007/review-01.jpg", b"crop")]
    with pytest.raises(ValueError, match="duration"):
        video_faces(
            LocalMedia("video/mp4", b"video", "hash"),
            settings(tmp_path, max_probe_video_duration_seconds=0.5),
            Detector(),
            Embedder(),
        )


def test_ephemeral_search_returns_names_and_no_embeddings(tmp_path, monkeypatch):
    path = tmp_path / "face.jpg"
    path.write_bytes(b"\xff\xd8\xffimage")
    database = Database()
    service = ProbeService(settings(tmp_path), database, Detector(), Embedder())
    embedding = [512**-0.5] * 512
    monkeypatch.setattr(
        "worker.probe_service.image_faces",
        lambda *args: (
            [
                {
                    "local_face_id": 0,
                    "bbox": (1, 2, 3, 4),
                    "detector_confidence": 0.9,
                    "start_ms": None,
                    "end_ms": None,
                    "observation_count": 1,
                    "max_quality": None,
                    "mean_quality": None,
                    "embedding": embedding,
                }
            ],
            [ReviewCrop("face-000000/review.jpg", b"crop")],
        ),
    )
    run = service.submit_local(path, 3)
    candidate = run.result["results"][0]["candidates"][0]
    assert candidate["display_name"] == "private-label"
    assert candidate["source_observation_count"] == 1
    assert "embedding" not in run.result["results"][0]
    assert database.calls[0][1:] == (settings(tmp_path).embedding_model_version, 3)


def test_no_face_succeeds_without_database_writes(tmp_path, monkeypatch):
    path = tmp_path / "face.jpg"
    path.write_bytes(b"\xff\xd8\xffimage")
    database = Database()
    service = ProbeService(settings(tmp_path), database, Detector(), Embedder())
    monkeypatch.setattr("worker.probe_service.image_faces", lambda *args: ([], []))
    run = service.submit_local(path, 3)
    assert run.result["detected_result_count"] == 0
    assert database.calls == []


def test_pre_detected_crop_directory_aggregates_without_detector(tmp_path, monkeypatch):
    crop_dir = tmp_path / "crops"
    crop_dir.mkdir()
    for name in ("b.jpg", "a.jpg"):
        (crop_dir / name).write_bytes(b"\xff\xd8\xffimage")
    monkeypatch.setattr(
        "worker.probe_service.decode_image",
        lambda *args: SimpleNamespace(shape=(10, 10, 3)),
    )
    monkeypatch.setattr("cv2.copyMakeBorder", lambda frame, *args: frame)
    detector = Detector([Detection((1, 1, 9, 9), 0.9)])
    database = Database()
    service = ProbeService(settings(tmp_path), database, detector, Embedder())
    run = service.submit_crop_directory(crop_dir, 2)
    assert run.result["input_mode"] == "pre_detected_crop_directory"
    assert run.result["results"][0]["observation_count"] == 2
    assert len(run.crops) == 2
    assert len(detector.detections) == 1


def test_review_output_is_private_and_non_overwriting(tmp_path):
    run = ProbeRun(
        {"status": "succeeded", "results": []},
        (ReviewCrop("face-000000/review.jpg", b"crop"),),
    )
    destination = tmp_path / "review"
    result, result_path = write_review(run, destination)
    crop = destination / "face-000000/review.jpg"
    assert result["review_directory"] == str(destination)
    assert os.stat(destination).st_mode & 0o777 == 0o700
    assert os.stat(crop).st_mode & 0o777 == 0o600
    assert os.stat(result_path).st_mode & 0o777 == 0o600
    assert "embedding" not in result_path.read_text()
    with pytest.raises(FileExistsError):
        write_review(run, destination)
