import hashlib
from types import SimpleNamespace

import numpy as np

from worker.gallery_backfill import (
    ExistingTrack,
    GalleryBackfill,
    RegeneratedTrack,
    associate_track,
)


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
        self.fallbacks = []

    def candidates(self, limit, model_version):
        assert limit == 10
        assert model_version == "model-v1"
        return [self.track]

    def active_ids(self, subject_id):
        return []

    def stage(self, *args):
        self.staged.append(args)

    def publish(self, subject_id, ids):
        self.published.append((subject_id, ids))

    def queue_fallback(self, existing, reason):
        self.fallbacks.append((existing.source_id, reason))


class Storage:
    def __init__(self, data):
        self.data = data

    def download_source_file(self, uri, generation, maximum, destination):
        destination.write_bytes(self.data)
        return len(self.data), hashlib.sha256(self.data).hexdigest()

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
    monkeypatch.setattr(
        "worker.gallery_backfill.targeted_crops",
        lambda *_args, **_kwargs: (
            {track.track_id: regenerated(4, 200, 200, np.array([1.0, 0.0]))},
            200,
            1000,
        ),
    )
    repository = Repository(track)
    report = GalleryBackfill(
        SimpleNamespace(
            max_video_bytes=100,
            detector_fps=8,
            gallery_repair_min_similarity=0.65,
            embedding_model_version="model-v1",
        ),
        repository,
        Storage(data),
        object(),
        object(),
    ).run(10)
    assert report["associated"] == 1
    assert report["subjects_published"] == 1
    assert len(repository.staged) == 1
    assert len(repository.published[0][1]) == 1


def test_interactive_single_frame_track_uses_original_tracking_provenance(
    tmp_path, monkeypatch
):
    from contextlib import nullcontext
    from dataclasses import replace

    import av

    from worker.subject_backfill import SubjectGalleryBackfill

    track = replace(
        existing(), start_ms=100, end_ms=100, local_track_id=7, content_type="video/mp4"
    )
    path = tmp_path / "source.mp4"
    path.write_bytes(b"video")
    face = {
        "local_face_id": 7,
        "start_ms": 100,
        "end_ms": 100,
        "embedding": track.embedding,
        "max_quality": 0.9,
    }
    crop = SimpleNamespace(relative_path="track-000007/review-01.jpg", content=b"jpeg")
    monkeypatch.setattr("worker.probe_service.video_faces", lambda *_: ([face], [crop]))
    monkeypatch.setattr(
        av, "open", lambda *_: nullcontext(SimpleNamespace(duration=1_000_000))
    )
    monkeypatch.setattr(GalleryBackfill, "regenerate", lambda *_: ({}, 0, 1000))
    backfill = SubjectGalleryBackfill(
        SimpleNamespace(max_probe_video_bytes=100), None, None, None, None
    )
    found, decoded, duration = backfill.regenerate(str(path), [track])
    assert found[track.track_id].crop_jpeg == b"jpeg"
    assert decoded == duration == 1000
    assert backfill.regenerate(str(path), [replace(track, local_track_id=8)])[0] == {}
    assert (
        backfill.regenerate(str(path), [replace(track, embedding=[0.0, 1.0])])[0] == {}
    )
