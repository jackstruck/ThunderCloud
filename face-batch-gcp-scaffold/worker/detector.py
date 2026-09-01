from __future__ import annotations

from .models import Detection


class ScrfdDetector:
    """Decode standard 3-stride SCRFD ONNX detector outputs."""

    def __init__(
        self,
        model_path,
        input_size: tuple[int, int] = (640, 640),
        threshold: float = 0.5,
    ):
        import onnxruntime as ort

        self.input_size = input_size
        self.threshold = threshold
        self.session = ort.InferenceSession(
            str(model_path), providers=["CUDAExecutionProvider", "CPUExecutionProvider"]
        )
        self.input_name = self.session.get_inputs()[0].name
        output_count = len(self.session.get_outputs())
        if output_count not in (6, 9):
            raise ValueError(f"unsupported SCRFD output count: {output_count}")
        self.has_landmarks = output_count == 9
        self.strides = (8, 16, 32)
        self.anchor_cache = {}

    def detect(self, frame) -> list[Detection]:
        import cv2
        import numpy as np

        input_width, input_height = self.input_size
        height, width = frame.shape[:2]
        scale = min(input_width / width, input_height / height)
        resized_width, resized_height = int(width * scale), int(height * scale)
        resized = cv2.resize(frame, (resized_width, resized_height))
        canvas = np.zeros((input_height, input_width, 3), dtype=np.uint8)
        canvas[:resized_height, :resized_width] = resized
        blob = cv2.dnn.blobFromImage(
            canvas, 1.0 / 128.0, self.input_size, (127.5, 127.5, 127.5), swapRB=True
        )
        outputs = self.session.run(None, {self.input_name: blob})
        candidates: list[Detection] = []
        levels = len(self.strides)
        for level, stride in enumerate(self.strides):
            scores = outputs[level].reshape(-1)
            boxes = outputs[level + levels].reshape(-1, 4) * stride
            keypoints = None
            if self.has_landmarks:
                keypoints = outputs[level + levels * 2].reshape(-1, 10) * stride
            feature_height, feature_width = (
                input_height // stride,
                input_width // stride,
            )
            cache_key = (feature_height, feature_width, stride, len(scores))
            centers = self.anchor_cache.get(cache_key)
            if centers is None:
                grid_x, grid_y = np.meshgrid(
                    np.arange(feature_width), np.arange(feature_height)
                )
                centers = np.stack((grid_x, grid_y), axis=-1).reshape(-1, 2) * stride
                repeats = max(1, len(scores) // len(centers))
                centers = np.repeat(centers, repeats, axis=0)
                self.anchor_cache[cache_key] = centers
            for index in np.where(scores >= self.threshold)[0]:
                center = centers[index]
                distance = boxes[index]
                bbox = (
                    max(0.0, float(center[0] - distance[0])) / scale,
                    max(0.0, float(center[1] - distance[1])) / scale,
                    min(float(width), float(center[0] + distance[2]) / scale),
                    min(float(height), float(center[1] + distance[3]) / scale),
                )
                landmarks = ()
                if keypoints is not None:
                    values = keypoints[index].reshape(5, 2)
                    landmarks = tuple(
                        (
                            float(center[0] + point[0]) / scale,
                            float(center[1] + point[1]) / scale,
                        )
                        for point in values
                    )
                candidates.append(Detection(bbox, float(scores[index]), landmarks))
        return _nms(candidates, 0.4)


def _nms(detections: list[Detection], threshold: float) -> list[Detection]:
    ordered = sorted(detections, key=lambda item: item.score, reverse=True)
    retained: list[Detection] = []
    while ordered:
        best = ordered.pop(0)
        retained.append(best)
        ordered = [
            candidate
            for candidate in ordered
            if _iou(best.bbox, candidate.bbox) < threshold
        ]
    return retained


def _iou(a, b) -> float:
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = area_a + area_b - intersection
    return intersection / union if union else 0.0
