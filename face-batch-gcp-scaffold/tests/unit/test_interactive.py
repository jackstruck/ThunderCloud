import hashlib
from types import SimpleNamespace

import cv2
import numpy as np

from worker.interactive import InteractiveProcessor
from worker.interactive_repository import DetectionWork, MatchGroup, MatchWork
from worker.models import Detection

RUN_ID = "00000000-0000-4000-8000-000000000000"


def jpeg():
    ok, encoded = cv2.imencode(".jpg", np.zeros((32, 32, 3), dtype=np.uint8))
    assert ok
    return encoded.tobytes()


class Detector:
    def __init__(self, faces=True):
        self.faces = faces

    def detect(self, _frame):
        return [Detection((4, 4, 28, 28), 0.9)] if self.faces else []


class Embedder:
    def embed(self, _frame, _detection):
        return np.ones(512, dtype=np.float32)


class Storage:
    bucket_name = "test-bucket"

    def __init__(self, data):
        self.data = data
        self.previews = []

    def download_temporary(self, name, generation, maximum):
        assert (name, generation) == ("submissions-temporary/run/source.jpg", 3)
        return self.data, hashlib.sha256(self.data).hexdigest()

    def upload_preview(self, run_id, group_id, content):
        self.previews.append((run_id, group_id, content))
        return f"submissions-temporary/{run_id}/previews/{group_id}.jpg", 5

    def promote_temporary(self, source_id, name, generation):
        self.promoted = (source_id, name, generation)
        return f"training-media/{source_id}/source.jpg", 8, len(self.data)


class Repository:
    def __init__(self, data):
        self.data = data
        self.detected = None
        self.failed = []
        self.match_completed = None
        self.existing_retained = None

    def claim_detection(self, run_id):
        return DetectionWork(
            "operation",
            run_id,
            "submissions-temporary/run/source.jpg",
            3,
            "image/jpeg",
            hashlib.sha256(self.data).hexdigest(),
            "owner",
        )

    def complete_detection(self, work, groups, **versions):
        self.detected = (work, groups, versions)
        return True

    def fail(self, run_id, step, code, retryable):
        self.failed.append((run_id, step, code, retryable))

    def claim_matching(self, run_id):
        return "owner", [MatchGroup("group", [1.0] * 512)]

    def rank(self, groups, version, top_k):
        return [
            [SimpleNamespace(subject_id="subject", similarity=0.9, display_name=None)]
        ]

    def complete_matching(self, run_id, owner, groups, rankings):
        self.match_completed = (run_id, owner, groups, rankings)
        return True

    def retained_source(self, _sha256):
        return self.existing_retained


def settings():
    return SimpleNamespace(
        detector_model=None,
        embedding_model=None,
        embedding_color_order="RGB",
        require_cuda=False,
        max_probe_image_bytes=1_000_000,
        max_probe_image_pixels=1_000_000,
        max_probe_video_bytes=1_000_000,
        max_probe_video_duration_seconds=60,
        detector_fps=8,
        best_n=5,
        detector_version="detector-v1",
        embedding_model_version="embedding-v1",
        top_k=10,
    )


def test_image_detection_persists_opaque_group_embedding_and_preview():
    data = jpeg()
    repository = Repository(data)
    storage = Storage(data)
    processor = InteractiveProcessor(
        settings(), repository, storage, Detector(), Embedder()
    )
    assert processor.detect(RUN_ID)
    group = repository.detected[1][0]
    assert group["bbox"] == (4, 4, 28, 28)
    assert len(group["embedding"]) == 512
    assert group["preview_generation"] == 5
    assert storage.previews[0][0] == RUN_ID


def test_zero_faces_completes_detection_without_groups():
    data = jpeg()
    repository = Repository(data)
    processor = InteractiveProcessor(
        settings(), repository, Storage(data), Detector(False), Embedder()
    )
    assert processor.detect(RUN_ID)
    assert repository.detected[1] == []


def test_checksum_mismatch_fails_terminally():
    data = jpeg()
    repository = Repository(data)
    repository.claim_detection = lambda run_id: DetectionWork(
        "operation",
        run_id,
        "submissions-temporary/run/source.jpg",
        3,
        "image/jpeg",
        "0" * 64,
        "owner",
    )
    processor = InteractiveProcessor(
        settings(), repository, Storage(data), Detector(), Embedder()
    )
    assert not processor.detect(RUN_ID)
    assert repository.failed == [(RUN_ID, "detecting", "invalid_media", False)]


def test_matching_uses_model_compatible_read_only_ranker():
    data = jpeg()
    repository = Repository(data)
    processor = InteractiveProcessor(
        settings(), repository, Storage(data), Detector(), Embedder()
    )
    assert processor.match(RUN_ID)
    assert repository.match_completed[0:2] == (RUN_ID, "owner")


def test_retained_matching_promotes_before_enrollment_commit():
    data = jpeg()
    repository = Repository(data)
    repository.claim_matching = lambda _run_id: MatchWork(
        "owner",
        [MatchGroup("group", [1.0] * 512)],
        "retain_and_enroll",
        "submissions-temporary/run/source.jpg",
        3,
        "image/jpeg",
        hashlib.sha256(data).hexdigest(),
        len(data),
    )
    calls = []
    repository.complete_matching = lambda *args, **kwargs: calls.append(kwargs) or True
    storage = Storage(data)
    processor = InteractiveProcessor(
        settings(), repository, storage, Detector(), Embedder()
    )
    assert processor.match(RUN_ID)
    assert storage.promoted[1:] == ("submissions-temporary/run/source.jpg", 3)
    assert "threshold_version" not in calls[0]
    assert "threshold" not in calls[0]


def test_enroll_only_does_not_promote_temporary_media():
    data = jpeg()
    repository = Repository(data)
    repository.claim_matching = lambda _run_id: MatchWork(
        "owner",
        [MatchGroup("group", [1.0] * 512)],
        "enroll_only",
        "submissions-temporary/run/source.jpg",
        3,
        "image/jpeg",
        hashlib.sha256(data).hexdigest(),
        len(data),
    )
    calls = []
    repository.complete_matching = lambda *args, **kwargs: calls.append(kwargs) or True
    storage = Storage(data)
    processor = InteractiveProcessor(
        settings(), repository, storage, Detector(), Embedder()
    )
    assert processor.match(RUN_ID)
    assert not hasattr(storage, "promoted")
    assert calls[0]["enrollment_source"][1:] == (None, None, None)
