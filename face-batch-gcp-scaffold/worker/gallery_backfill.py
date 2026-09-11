from __future__ import annotations

import os
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from google.api_core.exceptions import GoogleAPIError

from .pipeline import _face_crop_jpeg
from .quality import score_face
from .video import decoded_frames, frame_timestamp_ms


@dataclass(frozen=True)
class ExistingTrack:
    subject_id: str
    source_id: str
    track_id: str
    source_uri: str
    source_generation: int
    source_sha256: str
    start_ms: int
    end_ms: int
    embedding: Any
    model_version: str
    quality: float
    local_track_id: int | None = None
    content_type: str | None = None


@dataclass(frozen=True)
class RegeneratedTrack:
    local_id: int
    start_ms: int
    end_ms: int
    embedding: Any
    quality: float
    crop_jpeg: bytes


def _cosine(left, right) -> float:
    left_array, right_array = np.asarray(left), np.asarray(right)
    denominator = np.linalg.norm(left_array) * np.linalg.norm(right_array)
    return (
        0.0
        if denominator == 0
        else float(np.dot(left_array, right_array) / denominator)
    )


def _overlap(left_start: int, left_end: int, right_start: int, right_end: int) -> float:
    intersection = max(0, min(left_end, right_end) - max(left_start, right_start))
    union = max(left_end, right_end) - min(left_start, right_start)
    return 0.0 if union <= 0 else intersection / union


def associate_track(
    existing: ExistingTrack,
    regenerated: list[RegeneratedTrack],
    *,
    minimum_overlap: float = 0.35,
    minimum_similarity: float = 0.65,
    ambiguity_margin: float = 0.05,
) -> RegeneratedTrack | None:
    scored = []
    for candidate in regenerated:
        overlap = _overlap(
            existing.start_ms, existing.end_ms, candidate.start_ms, candidate.end_ms
        )
        similarity = _cosine(existing.embedding, candidate.embedding)
        if overlap >= minimum_overlap and similarity >= minimum_similarity:
            scored.append((overlap * similarity, similarity, overlap, candidate))
    scored.sort(key=lambda item: (-item[0], -item[1], -item[2], item[3].local_id))
    if not scored:
        return None
    if len(scored) > 1 and scored[0][0] - scored[1][0] < ambiguity_margin:
        return None
    return scored[0][3]


def merge_windows(tracks: list[ExistingTrack], margin_ms: int, duration_ms: int):
    windows = sorted(
        (max(0, track.start_ms - margin_ms), min(duration_ms, track.end_ms + margin_ms))
        for track in tracks
    )
    merged = []
    for start, end in windows:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def targeted_crops(
    path: str,
    tracks: list[ExistingTrack],
    detector,
    embedder,
    *,
    detector_fps: float,
    margin_ms: int,
    minimum_similarity: float,
    ambiguity_margin: float,
):
    import av
    import cv2

    image = cv2.imread(path)
    if image is not None:
        embedded = []
        for detection in detector.detect(image):
            try:
                embedded.append((detection, embedder.embed(image, detection)))
            except ValueError:
                continue
        best = {}
        for track in tracks:
            ranked = sorted(
                (
                    (_cosine(track.embedding, embedding), detection, embedding)
                    for detection, embedding in embedded
                ),
                key=lambda item: item[0],
                reverse=True,
            )
            if not ranked or ranked[0][0] < minimum_similarity:
                continue
            if len(ranked) > 1 and ranked[0][0] - ranked[1][0] < ambiguity_margin:
                continue
            _, detection, embedding = ranked[0]
            best[track.track_id] = RegeneratedTrack(
                0,
                0,
                0,
                embedding,
                score_face(image, detection).score,
                _face_crop_jpeg(image, detection.bbox),
            )
        return best, 0, 0

    best: dict[str, RegeneratedTrack] = {}
    decoded_ms = 0
    with av.open(path) as container:
        stream = container.streams.video[0]
        duration_ms = int(
            float(stream.duration * stream.time_base) * 1000
            if stream.duration is not None
            else float(container.duration or 0) / av.time_base * 1000
        )
        for window_start, window_end in merge_windows(tracks, margin_ms, duration_ms):
            decoded_ms += window_end - window_start
            container.seek(
                int(window_start / 1000 / float(stream.time_base)),
                stream=stream,
                backward=True,
                any_frame=False,
            )
            next_ms = window_start
            for frame in decoded_frames(container, stream):
                timestamp_ms = frame_timestamp_ms(frame, stream, next_ms)
                if timestamp_ms < window_start:
                    continue
                if timestamp_ms > window_end:
                    break
                if timestamp_ms < next_ms:
                    continue
                next_ms = timestamp_ms + 1000 / detector_fps
                image = frame.to_ndarray(format="bgr24")
                detections = detector.detect(image)
                embedded = []
                for detection in detections:
                    try:
                        embedded.append((detection, embedder.embed(image, detection)))
                    except ValueError:
                        continue
                for track in tracks:
                    if not track.start_ms <= timestamp_ms <= track.end_ms:
                        continue
                    ranked = sorted(
                        (
                            (_cosine(track.embedding, embedding), detection, embedding)
                            for detection, embedding in embedded
                        ),
                        reverse=True,
                        key=lambda value: value[0],
                    )
                    if not ranked or ranked[0][0] < minimum_similarity:
                        continue
                    if (
                        len(ranked) > 1
                        and ranked[0][0] - ranked[1][0] < ambiguity_margin
                    ):
                        continue
                    _, detection, embedding = ranked[0]
                    quality = score_face(image, detection).score
                    if (
                        track.track_id not in best
                        or quality > best[track.track_id].quality
                    ):
                        best[track.track_id] = RegeneratedTrack(
                            0,
                            timestamp_ms,
                            timestamp_ms,
                            embedding,
                            quality,
                            _face_crop_jpeg(image, detection.bbox),
                        )
    return best, decoded_ms, duration_ms


class GalleryBackfill:
    def __init__(self, settings, repository, storage, detector, embedder):
        self.settings = settings
        self.repository = repository
        self.storage = storage
        self.detector = detector
        self.embedder = embedder

    def regenerate(self, path, tracks):
        return targeted_crops(
            path,
            tracks,
            self.detector,
            self.embedder,
            detector_fps=self.settings.detector_fps,
            margin_ms=int(os.getenv("FACE_BACKFILL_WINDOW_MARGIN_MS", "1000")),
            minimum_similarity=self.settings.gallery_repair_min_similarity,
            ambiguity_margin=float(os.getenv("FACE_BACKFILL_AMBIGUITY_MARGIN", "0.05")),
        )

    def run(self, limit: int) -> dict[str, Any]:
        existing_tracks = self.repository.candidates(
            limit, self.settings.embedding_model_version
        )
        by_subject: dict[str, list[ExistingTrack]] = {}
        for track in existing_tracks:
            by_subject.setdefault(track.subject_id, []).append(track)
        started = time.monotonic()
        report: dict[str, Any] = {
            "subjects_scanned": len(by_subject),
            "tracks_scanned": len(existing_tracks),
            "associated": 0,
            "skipped": 0,
            "source_failures": 0,
            "subjects_published": 0,
            "source_bytes_scanned": 0,
            "decoded_window_ms": 0,
            "full_video_ms": 0,
            "fallback_sources": [],
        }
        source_cache: dict[tuple[str, int], dict[str, RegeneratedTrack] | None] = {}
        tracks_by_source: dict[tuple[str, int], list[ExistingTrack]] = {}
        for track in existing_tracks:
            tracks_by_source.setdefault(
                (track.source_uri, track.source_generation), []
            ).append(track)
        for subject_id, tracks in by_subject.items():
            publish_ids = self.repository.active_ids(subject_id)
            for existing in tracks:
                if len(publish_ids) >= 5:
                    break
                source_key = (existing.source_uri, existing.source_generation)
                regenerated = source_cache.get(source_key)
                if source_key not in source_cache:
                    try:
                        fd, path = tempfile.mkstemp(
                            prefix="gallery-backfill-", suffix=".mp4"
                        )
                        os.close(fd)
                        try:
                            size, digest = self.storage.download_source_file(
                                existing.source_uri,
                                existing.source_generation,
                                self.settings.max_video_bytes,
                                Path(path),
                            )
                            report["source_bytes_scanned"] += size
                            if digest != existing.source_sha256:
                                raise ValueError("backfill source checksum mismatch")
                            regenerated, decoded_ms, duration_ms = self.regenerate(
                                path, tracks_by_source[source_key]
                            )
                            report["decoded_window_ms"] += decoded_ms
                            report["full_video_ms"] += duration_ms
                        finally:
                            os.unlink(path)
                        source_cache[source_key] = regenerated
                    except (OSError, RuntimeError, ValueError, GoogleAPIError):
                        source_cache[source_key] = None
                        report["source_failures"] += 1
                        self.repository.queue_fallback(existing, "source_failure")
                        if existing.source_uri not in report["fallback_sources"]:
                            report["fallback_sources"].append(existing.source_uri)
                        regenerated = None
                if regenerated is None:
                    continue
                match = regenerated.get(existing.track_id)
                if match is None:
                    report["skipped"] += 1
                    self.repository.queue_fallback(existing, "no_unambiguous_crop")
                    if existing.source_uri not in report["fallback_sources"]:
                        report["fallback_sources"].append(existing.source_uri)
                    continue
                representative_id = str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"gallery:{existing.track_id}:{existing.source_generation}",
                    )
                )
                object_name, generation = self.storage.upload_gallery_face(
                    subject_id, representative_id, match.crop_jpeg
                )
                self.repository.stage(
                    existing,
                    match,
                    representative_id,
                    object_name,
                    generation,
                )
                publish_ids.append(representative_id)
                report["associated"] += 1
            if publish_ids:
                self.repository.publish(subject_id, publish_ids[:5])
                report["subjects_published"] += 1
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)
        report["association_rate"] = (
            report["associated"] / report["tracks_scanned"]
            if report["tracks_scanned"]
            else 0.0
        )
        report["avoided_full_video_ms"] = max(
            0, report["full_video_ms"] - report["decoded_window_ms"]
        )
        return report
