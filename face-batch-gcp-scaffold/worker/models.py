from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Detection:
    bbox: tuple[float, float, float, float]
    score: float
    landmarks: tuple[tuple[float, float], ...] = ()


@dataclass(frozen=True)
class TrackedDetection:
    track_id: int
    detection: Detection


@dataclass(frozen=True)
class QualityResult:
    score: float
    components: dict[str, float]


@dataclass(order=True)
class Candidate:
    quality: float
    timestamp_ms: int = field(compare=False)
    embedding: Any = field(compare=False)
    components: dict[str, float] = field(compare=False, default_factory=dict)
    crop_jpeg: bytes | None = field(compare=False, default=None, repr=False)


@dataclass
class TrackTemplate:
    local_track_id: int
    start_ms: int
    end_ms: int
    observation_count: int
    candidates: list[Candidate]
    embedding: Any

    @property
    def embedded_count(self) -> int:
        return len(self.candidates)

    @property
    def max_quality(self) -> float | None:
        return max((x.quality for x in self.candidates), default=None)

    @property
    def mean_quality(self) -> float | None:
        if not self.candidates:
            return None
        return sum(x.quality for x in self.candidates) / len(self.candidates)


@dataclass(frozen=True)
class Match:
    subject_id: str | None
    score: float | None
    decision: str
