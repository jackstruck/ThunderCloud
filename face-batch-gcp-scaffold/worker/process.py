from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from pathlib import Path

from .config import Settings
from .db import Database, Versions
from .detector import ScrfdDetector
from .embedder import OnnxFaceEmbedder
from .pipeline import process_video
from .storage import StorageRepository, load_configured_csek, validate_sha256
from .tracker import ByteTrackTracker


def idempotency_key(source_ref: str, sha256: str, settings: Settings) -> str:
    payload = (
        f"{source_ref}\0{sha256}\0{settings.worker_version}\0{settings.detector_version}"
        f"\0{settings.embedding_model_version}\0{settings.threshold_version}"
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def export_face_crops(
    output_dir: Path, job_id: str, external_source_ref: str, templates
) -> Path:
    job_dir = output_dir / job_id
    job_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
    manifest = []
    for template in templates:
        track_dir = job_dir / f"track-{template.local_track_id:06d}"
        track_dir.mkdir(mode=0o700)
        for rank, candidate in enumerate(template.candidates, start=1):
            if candidate.crop_jpeg is None:
                continue
            name = f"rank-{rank:02d}_time-{candidate.timestamp_ms:012d}ms.jpg"
            destination = track_dir / name
            destination.write_bytes(candidate.crop_jpeg)
            os.chmod(destination, 0o600)
            manifest.append(
                {
                    "file": str(destination.relative_to(job_dir)),
                    "track_id": template.local_track_id,
                    "timestamp_ms": candidate.timestamp_ms,
                    "quality": candidate.quality,
                    "quality_components": candidate.components,
                }
            )
    manifest_path = job_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {"job_id": job_id, "source": external_source_ref, "faces": manifest},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    os.chmod(manifest_path, 0o600)
    return job_dir


def run(
    *,
    gcs_uri: str,
    external_source_ref: str,
    expected_sha256: str | None,
    job_id: str,
    staging_generation: int | None = None,
    source_metadata: dict | None = None,
    settings: Settings | None = None,
) -> bool:
    settings = settings or Settings.from_env()
    settings.validate()
    uuid.UUID(job_id)
    expected_sha256 = validate_sha256(expected_sha256)
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
    # Validate the source reference separately so it can never be confused with the deletion target.
    storage.source_uri(external_source_ref)
    video_bytes, actual_sha256 = storage.download_staging(
        gcs_uri, staging_generation, settings.max_video_bytes
    )
    if expected_sha256 and actual_sha256 != expected_sha256:
        raise ValueError("staging object SHA-256 does not match expected value")

    detector = ScrfdDetector(settings.detector_model)
    tracker = ByteTrackTracker(frame_rate=max(1, round(settings.detector_fps)))
    embedder = OnnxFaceEmbedder(
        settings.embedding_model, color_order=settings.embedding_color_order
    )
    templates = process_video(
        video_bytes, detector, tracker, embedder, settings.detector_fps, settings.best_n
    )
    if settings.face_output_dir is not None:
        output_path = export_face_crops(
            settings.face_output_dir, job_id, external_source_ref, templates
        )
        logging.getLogger(__name__).info(
            "face_crops_exported", extra={"job_id": job_id, "path": str(output_path)}
        )
    database = Database(
        settings.cloud_sql_instance,
        settings.db_user,
        settings.db_name,
        settings.cloud_sql_ip_type,
    )
    try:
        wrote = database.commit_results(
            job_id=job_id,
            idempotency_key=idempotency_key(
                external_source_ref, actual_sha256, settings
            ),
            external_source_ref=external_source_ref,
            source_sha256=actual_sha256,
            source_metadata=source_metadata or {},
            versions=Versions(
                settings.worker_version,
                settings.detector_version,
                settings.embedding_model_version,
                settings.threshold_version,
            ),
            templates=templates,
            top_k=settings.top_k,
            threshold=settings.match_threshold,
            matching_enabled=settings.matching_enabled,
        )
    finally:
        database.close()
    # A previous successful idempotent run is also a committed result, so stale staging is safe to remove.
    storage.delete_staging(gcs_uri, staging_generation)
    logging.getLogger(__name__).info("processing_succeeded", extra={"job_id": job_id})
    return wrote
