import hashlib
from types import SimpleNamespace

import numpy as np

from worker.gallery_backfill import (
    ExistingTrack,
    GalleryBackfill,
    RegeneratedTrack,
    associate_track,
)
from worker.probe_service import ReviewCrop


def existing(embedding=None):
    return ExistingTrack(
        "00000000-0000-4000-8000-000000000001",
        "00000000-0000-4000-8000-000000000002",
        "00000000-0000-4000-8000-000000000003",
        "gs://bucket/videos/a.mp4",
        7,
        "a" * 64,
        100,
        300,
        embedding if embedding is not None else np.array([1.0, 0.0]),
        "model-v1",
        0.9,
    )


def regenerated(local_id, start, end, embedding):
    return RegeneratedTrack(local_id, start, end, embedding, 0.8, b"\xff\xd8\xffcrop")


def test_association_requires_overlap_and_embedding_similarity():
    match = associate_track(
        existing(),
        [
            regenerated(1, 100, 300, np.array([1.0, 0.0])),
            regenerated(2, 500, 700, np.array([1.0, 0.0])),
            regenerated(3, 100, 300, np.array([0.0, 1.0])),
        ],
    )
    assert match.local_id == 1


def test_ambiguous_association_is_skipped_not_reassigned():
    assert (
        associate_track(
            existing(),
            [
                regenerated(1, 100, 300, np.array([1.0, 0.0])),
                regenerated(2, 105, 305, np.array([1.0, 0.0])),
            ],
        )
        is None
    )


class Repository:
    def __init__(self, track):
        self.track = track
        self.staged = []
        self.published = []

    def candidates(self, limit):
        assert limit == 10
        return [self.track]

    def active_ids(self, subject_id):
        return []

    def stage(self, *args):
        self.staged.append(args)

    def publish(self, subject_id, ids):
        self.published.append((subject_id, ids))


class Storage:
    def __init__(self, data):
        self.data = data

    def download_source_generation(self, uri, generation, maximum):
        return self.data

    def upload_gallery_face(self, subject_id, representative_id, data):
        assert data == b"\xff\xd8\xffcrop"
        return f"subject-gallery/{subject_id}/{representative_id}.jpg", 11


def test_backfill_publishes_only_unambiguous_regeneration(monkeypatch):
    data = b"video"
    track = existing()
    track = ExistingTrack(
        track.subject_id,
        track.source_id,
        track.track_id,
        track.source_uri,
        track.source_generation,
        hashlib.sha256(data).hexdigest(),
        track.start_ms,
        track.end_ms,
        track.embedding,
        track.model_version,
        track.quality,
    )
    face = {
        "local_face_id": 4,
        "start_ms": 100,
        "end_ms": 300,
        "embedding": np.array([1.0, 0.0]),
        "max_quality": 0.8,
    }
    monkeypatch.setattr(
        "worker.probe_service.video_faces",
        lambda *_args: (
            [face],
            [ReviewCrop("track-000004/review-01.jpg", b"\xff\xd8\xffcrop")],
        ),
    )
    repository = Repository(track)
    report = GalleryBackfill(
        SimpleNamespace(max_video_bytes=100),
        repository,
        Storage(data),
        object(),
        object(),
    ).run(10)
    assert report["associated"] == 1
    assert report["subjects_published"] == 1
    assert len(repository.staged) == 1
    assert len(repository.published[0][1]) == 1
