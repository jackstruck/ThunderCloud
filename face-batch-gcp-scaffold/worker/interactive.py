from __future__ import annotations

import json
import math
import os
import uuid

import cv2
import numpy as np

from .config import Settings
from .db import Database
from .detector import ScrfdDetector
from .embedder import OnnxFaceEmbedder
from .gallery_backfill import BackfillRepository, GalleryBackfill
from .interactive_repository import InteractiveRepository
from .job_invoker import CloudRunJobInvoker
from .probe_service import LocalMedia, image_faces, video_faces
from .storage import StorageRepository, load_configured_csek
from .telemetry import configure_logging


def _review_preview(crops) -> bytes:
    """Return one JPEG, or a compact numbered contact sheet for a video track."""
    if not crops:
        raise RuntimeError("detected group has no review crop")
    if len(crops) == 1:
        return crops[0].content
    tiles = []
    for index, crop in enumerate(crops[:6], 1):
        image = cv2.imdecode(
            np.frombuffer(crop.content, dtype=np.uint8), cv2.IMREAD_COLOR
        )
        if image is None:
            raise RuntimeError("detected group has an invalid review crop")
        height, width = image.shape[:2]
        scale = min(160 / max(width, 1), 160 / max(height, 1))
        resized = cv2.resize(
            image,
            (max(1, round(width * scale)), max(1, round(height * scale))),
            interpolation=cv2.INTER_AREA,
        )
        tile = np.zeros((180, 180, 3), dtype=np.uint8)
        y = 10 + (160 - resized.shape[0]) // 2
        x = 10 + (160 - resized.shape[1]) // 2
        tile[y : y + resized.shape[0], x : x + resized.shape[1]] = resized
        cv2.putText(
            tile,
            str(index),
            (14, 28),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )
        tiles.append(tile)
    columns = min(3, len(tiles))
    rows = math.ceil(len(tiles) / columns)
    sheet = np.zeros((rows * 180, columns * 180, 3), dtype=np.uint8)
    for index, tile in enumerate(tiles):
        row, column = divmod(index, columns)
        sheet[row * 180 : (row + 1) * 180, column * 180 : (column + 1) * 180] = tile
    ok, encoded = cv2.imencode(".jpg", sheet, [cv2.IMWRITE_JPEG_QUALITY, 88])
    if not ok:
        raise RuntimeError("failed to encode review contact sheet")
    return encoded.tobytes()


class InteractiveProcessor:
    def __init__(self, settings, repository, storage, detector=None, embedder=None):
        self.settings = settings
        self.repository = repository
        self.storage = storage
        self.detector = detector or ScrfdDetector(settings.detector_model)
        self.embedder = embedder or OnnxFaceEmbedder(
            settings.embedding_model, settings.embedding_color_order
        )
        if settings.require_cuda:
            for name, model in (
                ("detector", self.detector),
                ("embedder", self.embedder),
            ):
                if model.session.get_providers()[0] != "CUDAExecutionProvider":
                    raise RuntimeError(f"{name} did not initialize on CUDA")

    def detect(self, run_id: str) -> bool:
        work = self.repository.claim_detection(run_id)
        if work is None:
            return False
        try:
            maximum = (
                self.settings.max_probe_image_bytes
                if work.content_type.startswith("image/")
                else self.settings.max_probe_video_bytes
            )
            data, digest = self.storage.download_temporary(
                work.object_name, work.generation, maximum
            )
            if digest != work.sha256:
                raise ValueError("temporary media SHA-256 mismatch")
            media = LocalMedia(work.content_type, data, digest)
            if work.content_type.startswith("image/"):
                faces, crops = image_faces(
                    media, self.settings, self.detector, self.embedder
                )
                prefix = "face"
            else:
                faces, crops = video_faces(
                    media, self.settings, self.detector, self.embedder
                )
                prefix = "track"
            groups = []
            for face in faces:
                group_id = str(uuid.uuid4())
                crop_prefix = f"{prefix}-{face['local_face_id']:06d}/"
                review_crops = [
                    item
                    for item in crops
                    if item.relative_path.startswith(crop_prefix)
                ]
                preview_name, preview_generation = self.storage.upload_preview(
                    work.run_id, group_id, _review_preview(review_crops)
                )
                groups.append(
                    {
                        "group_id": group_id,
                        "local_group_id": face["local_face_id"],
                        "bbox": face["bbox"],
                        "start_ms": face["start_ms"],
                        "end_ms": face["end_ms"],
                        "quality": {
                            "detector_confidence": face["detector_confidence"],
                            "observation_count": face["observation_count"],
                            "max_quality": face["max_quality"],
                            "mean_quality": face["mean_quality"],
                        },
                        "preview_object_name": preview_name,
                        "preview_generation": preview_generation,
                        "embedding": face["embedding"],
                    },
                )
            return self.repository.complete_detection(
                work,
                groups,
                detector_version=self.settings.detector_version,
                embedding_version=self.settings.embedding_model_version,
            )
        except ValueError:
            self.repository.fail(run_id, "detecting", "invalid_media", False)
            return False
        except RuntimeError:
            self.repository.fail(run_id, "detecting", "detection_failed", True)
            return False

    def match(self, run_id: str) -> bool:
        claimed = self.repository.claim_matching(run_id)
        if claimed is None:
            return False
        # Tuple support keeps lightweight repository adapters usable.
        owner = claimed.lease_owner if hasattr(claimed, "lease_owner") else claimed[0]
        groups = claimed.groups if hasattr(claimed, "groups") else claimed[1]
        try:
            rankings = self.repository.rank(
                groups, self.settings.embedding_model_version, self.settings.top_k
            )
            enrollment_source = None
            policy = getattr(claimed, "handling_policy", "search_then_discard")
            if policy in {"retain_and_enroll", "enroll_only"}:
                if (not self.settings.matching_enabled or
                        self.settings.threshold_version == "unvalidated-v1"):
                    raise RuntimeError("enrollment gate is not configured")
                if policy == "retain_and_enroll":
                    enrollment_source = self.repository.retained_source(claimed.sha256)
                if enrollment_source is None:
                    source_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"face-run:{run_id}"))
                    enrollment_source = (
                        (source_id, *self.storage.promote_temporary(
                            source_id, claimed.object_name, claimed.generation
                        ))
                        if policy == "retain_and_enroll"
                        else (source_id, None, None, None)
                    )
            if enrollment_source is None:
                return self.repository.complete_matching(run_id, owner, groups, rankings)
            return self.repository.complete_matching(
                run_id, owner, groups, rankings, enrollment_source=enrollment_source,
                threshold=self.settings.match_threshold,
                threshold_version=self.settings.threshold_version,
                model_version=self.settings.embedding_model_version,
            )
        except RuntimeError:
            self.repository.fail(run_id, "matching", "matching_failed", True)
            return False


def main() -> None:
    configure_logging()
    mode = os.getenv("FACE_INTERACTIVE_MODE", "detect")
    if mode not in {"detect", "match", "backfill"}:
        raise ValueError("FACE_INTERACTIVE_MODE must be detect, match, or backfill")
    run_id = os.getenv("FACE_RUN_ID")
    if mode in {"detect", "match"}:
        if run_id is None:
            raise ValueError("FACE_RUN_ID is required for detect and match modes")
        uuid.UUID(run_id)
    settings = Settings.from_env()
    settings.validate()
    database = Database(
        settings.cloud_sql_instance,
        settings.db_user,
        settings.db_name,
        settings.cloud_sql_ip_type,
    )
    csek = load_configured_csek(
        settings.project_id, settings.csek_file, settings.csek_secret
    )
    storage = StorageRepository(
        settings.project_id,
        settings.bucket,
        settings.source_prefix,
        settings.staging_prefix,
        csek,
    )
    try:
        if mode == "backfill":
            detector = ScrfdDetector(settings.detector_model)
            embedder = OnnxFaceEmbedder(
                settings.embedding_model, settings.embedding_color_order
            )
            if settings.require_cuda:
                for name, model in (("detector", detector), ("embedder", embedder)):
                    if model.session.get_providers()[0] != "CUDAExecutionProvider":
                        raise RuntimeError(f"{name} did not initialize on CUDA")
            report = GalleryBackfill(
                settings,
                BackfillRepository(database),
                storage,
                detector,
                embedder,
            ).run(int(os.getenv("FACE_BACKFILL_LIMIT", "25")))
            print(json.dumps(report, sort_keys=True, separators=(",", ":")))
        else:
            processor = InteractiveProcessor(
                settings, InteractiveRepository(database), storage
            )
            if mode == "detect":
                completed = processor.detect(run_id)
            else:
                completed = processor.match(run_id)
            if completed:
                CloudRunJobInvoker(
                    settings.project_id,
                    os.getenv("FACE_REGION", "us-central1"),
                    os.getenv("FACE_INGEST_JOB", "face-ingest-drain"),
                    env={"FACE_INGEST_MODE": "maintenance"},
                )(run_id)
    finally:
        database.close()


if __name__ == "__main__":
    main()
