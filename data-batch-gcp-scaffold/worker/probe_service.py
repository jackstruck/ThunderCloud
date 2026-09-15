from __future__ import annotations

import hashlib
import io
import os
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .aggregate import l2_normalize
from .config import Settings
from .db import Database
from .detector import ScrfdDetector
from .embedder import OnnxFaceEmbedder
from .pipeline import _face_crop_jpeg, process_video
from .tracker import ByteTrackTracker

ALLOWED_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".mp4": "video/mp4",
}


@dataclass(frozen=True)
class LocalMedia:
    content_type: str
    data: bytes
    sha256: str

    @property
    def size(self) -> int:
        return len(self.data)


@dataclass(frozen=True)
class ReviewCrop:
    relative_path: str
    content: bytes


@dataclass(frozen=True)
class ProbeRun:
    result: dict[str, Any]
    crops: tuple[ReviewCrop, ...]


def _signature_type(header: bytes) -> str | None:
    if header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(header) >= 12 and header[4:8] == b"ftyp":
        return "video/mp4"
    return None


def read_local_media(path: Path, settings: Settings) -> LocalMedia:
    absolute = path.absolute()
    info = absolute.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ValueError("probe input must be a regular non-symlink file")
    content_type = ALLOWED_TYPES.get(absolute.suffix.lower())
    if content_type is None:
        raise ValueError("unsupported probe file type; use JPEG, PNG, or MP4")
    maximum = (
        settings.max_probe_image_bytes
        if content_type.startswith("image/")
        else settings.max_probe_video_bytes
    )
    if info.st_size <= 0 or info.st_size > maximum:
        raise ValueError("probe file exceeds configured size limit or is empty")
    descriptor = os.open(absolute, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (
            info.st_dev,
            info.st_ino,
        ):
            raise ValueError("probe input changed while it was opened")
        with os.fdopen(descriptor, "rb", closefd=False) as source:
            data = source.read(maximum + 1)
    finally:
        os.close(descriptor)
    if len(data) > maximum:
        raise ValueError("probe file exceeds configured size limit")
    if _signature_type(data[:16]) != content_type:
        raise ValueError("probe extension does not match its media signature")
    return LocalMedia(content_type, data, hashlib.sha256(data).hexdigest())


def decode_image(data: bytes, content_type: str, max_pixels: int):
    import cv2
    import numpy as np

    if content_type not in {"image/jpeg", "image/png"}:
        raise ValueError("unsupported image content type")
    frame = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if frame is None or frame.ndim != 3 or frame.shape[2] != 3:
        raise ValueError("invalid image data")
    if frame.shape[0] * frame.shape[1] > max_pixels:
        raise ValueError("probe image exceeds configured pixel limit")
    return frame


def image_faces(media: LocalMedia, settings: Settings, detector, embedder):
    frame = decode_image(
        media.data, media.content_type, settings.max_probe_image_pixels
    )
    faces, crops = [], []
    for local_id, detection in enumerate(detector.detect(frame)):
        faces.append(
            {
                "local_face_id": local_id,
                "bbox": detection.bbox,
                "detector_confidence": detection.score,
                "start_ms": None,
                "end_ms": None,
                "observation_count": 1,
                "max_quality": None,
                "mean_quality": None,
                "embedding": embedder.embed(frame, detection),
            }
        )
        crops.append(
            ReviewCrop(
                f"face-{local_id:06d}/review.jpg",
                _face_crop_jpeg(frame, detection.bbox),
            )
        )
    return faces, crops


def video_faces(media: LocalMedia, settings: Settings, detector, embedder):
    import av

    with av.open(io.BytesIO(media.data), mode="r") as container:
        if container.duration is None:
            raise ValueError("probe video duration is unavailable")
        if (
            container.duration / av.time_base
            > settings.max_probe_video_duration_seconds
        ):
            raise ValueError("probe video exceeds configured duration limit")
    templates = process_video(
        media.data,
        detector,
        ByteTrackTracker(max(1, round(settings.detector_fps))),
        embedder,
        settings.detector_fps,
        settings.best_n,
    )
    faces, crops = [], []
    for template in templates:
        faces.append(
            {
                "local_face_id": template.local_track_id,
                "bbox": None,
                "detector_confidence": None,
                "start_ms": template.start_ms,
                "end_ms": template.end_ms,
                "observation_count": template.observation_count,
                "max_quality": template.max_quality,
                "mean_quality": template.mean_quality,
                "embedding": template.embedding,
            }
        )
        for rank, candidate in enumerate(template.candidates, 1):
            if candidate.crop_jpeg is not None:
                crops.append(
                    ReviewCrop(
                        f"track-{template.local_track_id:06d}/review-{rank:02d}.jpg",
                        candidate.crop_jpeg,
                    )
                )
    return faces, crops


def _safe_observation(observation: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "video_uri",
        "source_sha256",
        "track_id",
        "start_ms",
        "end_ms",
        "max_quality",
        "mean_quality",
        "processing_completed_at",
    )
    return {key: observation[key] for key in keys}


class ProbeService:
    def __init__(self, settings: Settings, database=None, detector=None, embedder=None):
        settings.validate()
        self.settings = settings
        self.database = database or Database(
            settings.cloud_sql_instance,
            settings.db_user,
            settings.db_name,
            settings.cloud_sql_ip_type,
        )
        self.detector, self.embedder = detector, embedder

    def close(self) -> None:
        self.database.close()

    def _models(self) -> None:
        if self.detector is not None:
            return
        self.detector = ScrfdDetector(self.settings.detector_model)
        self.embedder = OnnxFaceEmbedder(
            self.settings.embedding_model, self.settings.embedding_color_order
        )
        if self.settings.require_cuda:
            for name, model in (
                ("detector", self.detector),
                ("embedder", self.embedder),
            ):
                if model.session.get_providers()[0] != "CUDAExecutionProvider":
                    raise RuntimeError(f"{name} did not initialize on CUDA")

    def _rank(
        self,
        faces,
        crops,
        media_kind: str,
        top_k: int,
        media_sha256: str,
        media_bytes: int,
        input_mode: str,
    ) -> ProbeRun:
        ranked_groups = (
            self.database.search_subjects(
                [face["embedding"] for face in faces],
                self.settings.embedding_model_version,
                top_k,
            )
            if faces
            else []
        )
        results = []
        prefix = "face-" if media_kind == "image" else "track-"
        for face, ranked in zip(faces, ranked_groups, strict=True):
            safe_face = {
                key: value for key, value in face.items() if key != "embedding"
            }
            safe_face["decision"] = "candidates_only"
            crop_prefix = prefix + f"{face['local_face_id']:06d}/"
            safe_face["review_crops"] = [
                crop.relative_path
                for crop in crops
                if crop.relative_path.startswith(crop_prefix)
            ]
            safe_face["candidates"] = []
            for rank, candidate in enumerate(ranked, 1):
                observations = [
                    _safe_observation(item) for item in candidate.observations
                ]
                safe_face["candidates"].append(
                    {
                        "rank": rank,
                        "subject_id": candidate.subject_id,
                        "display_name": candidate.display_name,
                        "similarity": candidate.similarity,
                        "source_video_count": len(
                            {item["video_uri"] for item in observations}
                        ),
                        "source_observation_count": len(observations),
                        "source_observations": observations,
                    }
                )
            results.append(safe_face)
        return ProbeRun(
            {
                "run_id": str(uuid.uuid4()),
                "status": "succeeded",
                "input_mode": input_mode,
                "media_kind": media_kind,
                "media_sha256": media_sha256,
                "media_bytes": media_bytes,
                "detected_result_count": len(results),
                "detector_version": self.settings.detector_version,
                "embedding_model_version": self.settings.embedding_model_version,
                "requested_top_k": top_k,
                "decision_policy": "candidates_only",
                "results": results,
            },
            tuple(crops),
        )

    def submit_local(self, path: Path, top_k: int) -> ProbeRun:
        if not 1 <= top_k <= 100:
            raise ValueError("top_k must be between 1 and 100")
        media = read_local_media(path, self.settings)
        self._models()
        if media.content_type.startswith("image/"):
            faces, crops = image_faces(
                media, self.settings, self.detector, self.embedder
            )
            media_kind = "image"
        else:
            faces, crops = video_faces(
                media, self.settings, self.detector, self.embedder
            )
            media_kind = "video"
        return self._rank(
            faces, crops, media_kind, top_k, media.sha256, media.size, "detected_media"
        )

    def submit_crop_directory(self, path: Path, top_k: int) -> ProbeRun:
        if not 1 <= top_k <= 100:
            raise ValueError("top_k must be between 1 and 100")
        directory = path.absolute()
        info = directory.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise ValueError("crop input must be a regular non-symlink directory")
        paths = sorted(
            item
            for item in directory.iterdir()
            if item.suffix.lower() in {".jpg", ".jpeg", ".png"}
        )
        if not paths or len(paths) > 1000:
            raise ValueError(
                "crop directory must contain between 1 and 1000 supported images"
            )
        self._models()
        embeddings, crops, digest, total = [], [], hashlib.sha256(), 0
        for rank, crop_path in enumerate(paths, 1):
            media = read_local_media(crop_path, self.settings)
            total += media.size
            if total > self.settings.max_probe_video_bytes:
                raise ValueError(
                    "crop directory exceeds configured aggregate byte limit"
                )
            frame = decode_image(
                media.data, media.content_type, self.settings.max_probe_image_pixels
            )
            import cv2

            height, width = frame.shape[:2]
            pad_y, pad_x = max(1, height // 4), max(1, width // 4)
            padded = cv2.copyMakeBorder(
                frame, pad_y, pad_y, pad_x, pad_x, cv2.BORDER_REFLECT_101
            )
            detections = self.detector.detect(padded)
            if not detections:
                raise ValueError(
                    f"pre-detected crop {rank} could not be confirmed by SCRFD"
                )
            center_x, center_y = pad_x + width / 2, pad_y + height / 2
            detection = min(
                detections,
                key=lambda item: (
                    ((item.bbox[0] + item.bbox[2]) / 2 - center_x) ** 2
                    + ((item.bbox[1] + item.bbox[3]) / 2 - center_y) ** 2,
                    -item.score,
                ),
            )
            embedding = self.embedder.embed(padded, detection)
            embeddings.append(embedding)
            digest.update(bytes.fromhex(media.sha256))
            extension = ".png" if media.content_type == "image/png" else ".jpg"
            crops.append(
                ReviewCrop(f"track-000000/review-{rank:02d}{extension}", media.data)
            )
        import numpy as np

        aggregate = l2_normalize(np.mean(np.asarray(embeddings), axis=0))
        face = {
            "local_face_id": 0,
            "bbox": None,
            "detector_confidence": None,
            "start_ms": None,
            "end_ms": None,
            "observation_count": len(embeddings),
            "max_quality": None,
            "mean_quality": None,
            "embedding": aggregate,
        }
        return self._rank(
            [face],
            crops,
            "image_crop_set",
            top_k,
            digest.hexdigest(),
            total,
            "pre_detected_crop_directory",
        )
