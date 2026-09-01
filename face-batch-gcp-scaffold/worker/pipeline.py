from __future__ import annotations

from dataclasses import dataclass

from .aggregate import BestCandidates, quality_weighted_mean
from .models import Candidate, TrackTemplate
from .quality import score_face
from .video import sampled_frames


def _face_crop_jpeg(frame, bbox) -> bytes:
    import cv2

    height, width = frame.shape[:2]
    x1, y1, x2, y2 = bbox
    padding = 0.15 * max(x2 - x1, y2 - y1)
    left = max(0, int(x1 - padding))
    top = max(0, int(y1 - padding))
    right = min(width, int(x2 + padding))
    bottom = min(height, int(y2 + padding))
    if right <= left or bottom <= top:
        raise ValueError("detected face has an empty crop")
    ok, encoded = cv2.imencode(".jpg", frame[top:bottom, left:right])
    if not ok:
        raise ValueError("could not encode detected face crop")
    return encoded.tobytes()


@dataclass
class _TrackState:
    start_ms: int
    end_ms: int
    observations: int
    candidates: BestCandidates


def process_video(
    video_bytes: bytes, detector, tracker, embedder, detector_fps: float, best_n: int
) -> list[TrackTemplate]:
    tracks: dict[int, _TrackState] = {}
    for timestamp_ms, frame in sampled_frames(video_bytes, detector_fps):
        detections = detector.detect(frame)
        for tracked in tracker.update(detections):
            state = tracks.get(tracked.track_id)
            if state is None:
                state = _TrackState(
                    timestamp_ms, timestamp_ms, 0, BestCandidates(best_n)
                )
                tracks[tracked.track_id] = state
            state.end_ms = timestamp_ms
            state.observations += 1
            quality = score_face(frame, tracked.detection)
            if not state.candidates.would_retain(quality.score):
                continue
            embedding = embedder.embed(frame, tracked.detection)
            state.candidates.add(
                Candidate(
                    quality.score,
                    timestamp_ms,
                    embedding,
                    quality.components,
                    _face_crop_jpeg(frame, tracked.detection.bbox),
                )
            )

    templates = []
    for track_id, state in sorted(tracks.items()):
        candidates = state.candidates.values()
        if not candidates:
            continue
        templates.append(
            TrackTemplate(
                local_track_id=track_id,
                start_ms=state.start_ms,
                end_ms=state.end_ms,
                observation_count=state.observations,
                candidates=candidates,
                embedding=quality_weighted_mean(candidates),
            )
        )
    return templates
