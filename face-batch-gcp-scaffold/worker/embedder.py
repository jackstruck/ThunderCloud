from __future__ import annotations

from .aggregate import l2_normalize

REFERENCE_LANDMARKS = [
    [38.2946, 51.6963],
    [73.5318, 51.5014],
    [56.0252, 71.7366],
    [41.5493, 92.3655],
    [70.7299, 92.2041],
]


class OnnxFaceEmbedder:
    """Model-agnostic 112x112 ONNX adapter suitable for exported AdaFace/CVLFace checkpoints."""

    def __init__(self, model_path, color_order: str = "RGB"):
        import onnxruntime as ort

        providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        self.session = ort.InferenceSession(str(model_path), providers=providers)
        self.input_name = self.session.get_inputs()[0].name
        self.color_order = color_order.upper()
        if self.color_order not in {"RGB", "BGR"}:
            raise ValueError("color order must be RGB or BGR")

    def embed(self, frame, detection):
        import cv2
        import numpy as np

        if len(detection.landmarks) >= 5:
            source = np.asarray(detection.landmarks[:5], dtype=np.float32)
            target = np.asarray(REFERENCE_LANDMARKS, dtype=np.float32)
            transform, _ = cv2.estimateAffinePartial2D(source, target, method=cv2.LMEDS)
            if transform is None:
                raise ValueError("could not align face landmarks")
            face = cv2.warpAffine(frame, transform, (112, 112), borderValue=0)
        else:
            x1, y1, x2, y2 = (int(x) for x in detection.bbox)
            face = cv2.resize(
                frame[max(0, y1) : max(y1 + 1, y2), max(0, x1) : max(x1 + 1, x2)],
                (112, 112),
            )
        model_input = (
            cv2.cvtColor(face, cv2.COLOR_BGR2RGB)
            if self.color_order == "RGB"
            else face
        ).astype(np.float32)
        tensor = ((model_input - 127.5) / 127.5).transpose(2, 0, 1)[None, ...]
        output = self.session.run(None, {self.input_name: tensor})[0].reshape(-1)
        if len(output) != 512:
            raise ValueError(
                f"embedding model returned {len(output)} dimensions, expected 512"
            )
        return l2_normalize(output)
