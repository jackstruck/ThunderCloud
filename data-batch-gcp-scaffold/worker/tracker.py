from __future__ import annotations

from .models import Detection, TrackedDetection


class ByteTrackTracker:
    def __init__(self, frame_rate: int = 30):
        import supervision as sv

        self.sv = sv
        self.tracker = sv.ByteTrack(frame_rate=frame_rate)

    def update(self, detections: list[Detection]) -> list[TrackedDetection]:
        import numpy as np

        if not detections:
            self.tracker.update_with_detections(self.sv.Detections.empty())
            return []
        sv_detections = self.sv.Detections(
            xyxy=np.asarray([x.bbox for x in detections], dtype=np.float32),
            confidence=np.asarray([x.score for x in detections], dtype=np.float32),
            class_id=np.zeros(len(detections), dtype=int),
        )
        tracked = self.tracker.update_with_detections(sv_detections)
        result = []
        if tracked.tracker_id is None:
            return result
        for box, confidence, tracker_id in zip(
            tracked.xyxy, tracked.confidence, tracked.tracker_id
        ):
            # Match ByteTrack output back to the highest-IoU original detection so landmarks survive.
            original = max(detections, key=lambda x: _iou(tuple(box), x.bbox))
            result.append(
                TrackedDetection(
                    int(tracker_id),
                    Detection(
                        tuple(float(x) for x in box),
                        float(confidence),
                        original.landmarks,
                    ),
                )
            )
        return result


def _iou(a, b) -> float:
    x1, y1, x2, y2 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = area_a + area_b - intersection
    return intersection / union if union > 0 else 0.0
