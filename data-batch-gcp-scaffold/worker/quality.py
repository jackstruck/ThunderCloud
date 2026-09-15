from __future__ import annotations

import math

from .models import Detection, QualityResult


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def score_face(frame, detection: Detection) -> QualityResult:
    import cv2
    import numpy as np

    height, width = frame.shape[:2]
    x1, y1, x2, y2 = detection.bbox
    ix1, iy1 = max(0, int(x1)), max(0, int(y1))
    ix2, iy2 = min(width, int(x2)), min(height, int(y2))
    if ix2 <= ix1 or iy2 <= iy1:
        return QualityResult(0.0, {"valid_crop": 0.0})
    crop = frame[iy1:iy2, ix1:ix2]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    face_pixels = min(ix2 - ix1, iy2 - iy1)
    size = _clamp((face_pixels - 24) / 136)
    sharpness_raw = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    sharpness = _clamp(math.log1p(sharpness_raw) / math.log1p(500.0))
    brightness_mean = float(np.mean(gray))
    brightness = _clamp(1.0 - abs(brightness_mean - 127.5) / 127.5)
    contrast = _clamp(float(np.std(gray)) / 64.0)
    landmark = 1.0 if len(detection.landmarks) >= 5 else 0.5
    pose = _pose_symmetry(detection.landmarks)
    components = {
        "detector": _clamp(detection.score),
        "size": size,
        "sharpness": sharpness,
        "brightness": brightness,
        "contrast": contrast,
        "landmark": landmark,
        "pose": pose,
    }
    weights = {
        "detector": 0.25,
        "size": 0.20,
        "sharpness": 0.20,
        "brightness": 0.10,
        "contrast": 0.10,
        "landmark": 0.05,
        "pose": 0.10,
    }
    return QualityResult(sum(components[k] * weights[k] for k in weights), components)


def _pose_symmetry(landmarks) -> float:
    if len(landmarks) < 5:
        return 0.5
    left_eye, right_eye, nose, left_mouth, right_mouth = landmarks[:5]
    eye_width = max(1.0, abs(right_eye[0] - left_eye[0]))
    mouth_width = max(1.0, abs(right_mouth[0] - left_mouth[0]))
    eye_midpoint = (left_eye[0] + right_eye[0]) / 2
    mouth_midpoint = (left_mouth[0] + right_mouth[0]) / 2
    horizontal_offset = (
        abs(nose[0] - eye_midpoint) / eye_width
        + abs(nose[0] - mouth_midpoint) / mouth_width
    ) / 2
    eye_tilt = abs(right_eye[1] - left_eye[1]) / eye_width
    mouth_tilt = abs(right_mouth[1] - left_mouth[1]) / mouth_width
    return _clamp(1.0 - horizontal_offset - (eye_tilt + mouth_tilt) / 2)
