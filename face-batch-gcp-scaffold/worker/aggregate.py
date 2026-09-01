from __future__ import annotations

import heapq
import math

from .models import Candidate


def l2_normalize(vector):
    norm = math.sqrt(sum(float(x) * float(x) for x in vector))
    if not math.isfinite(norm) or norm <= 0:
        raise ValueError("cannot normalize a zero or non-finite vector")
    return [float(x) / norm for x in vector]


class BestCandidates:
    def __init__(self, capacity: int):
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self.capacity = capacity
        self._heap: list[Candidate] = []

    def minimum_quality(self) -> float:
        return (
            self._heap[0].quality if len(self._heap) == self.capacity else float("-inf")
        )

    def would_retain(self, quality: float) -> bool:
        return len(self._heap) < self.capacity or quality > self._heap[0].quality

    def add(self, candidate: Candidate) -> bool:
        if len(self._heap) < self.capacity:
            heapq.heappush(self._heap, candidate)
            return True
        if candidate.quality <= self._heap[0].quality:
            return False
        heapq.heapreplace(self._heap, candidate)
        return True

    def values(self) -> list[Candidate]:
        return sorted(self._heap, reverse=True)


def quality_weighted_mean(candidates: list[Candidate]):
    if not candidates:
        raise ValueError("at least one candidate is required")
    dimensions = len(candidates[0].embedding)
    if dimensions == 0 or any(len(x.embedding) != dimensions for x in candidates):
        raise ValueError("candidate embeddings must have one common non-zero dimension")
    weights = [max(0.0, min(1.0, float(x.quality))) for x in candidates]
    total = sum(weights)
    if total <= 0:
        weights = [1.0] * len(candidates)
        total = float(len(candidates))
    mean = [
        sum(
            weight * float(candidate.embedding[i])
            for weight, candidate in zip(weights, candidates)
        )
        / total
        for i in range(dimensions)
    ]
    return l2_normalize(mean)
