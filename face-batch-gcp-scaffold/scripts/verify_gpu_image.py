#!/usr/bin/env python3
"""Verify packaged face models and require real CUDA execution unless opted out."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

import numpy as np
import onnxruntime as ort


MODELS = {
    Path("/opt/models/scrfd.onnx"): "5838f7fe053675b1c7a08b633df49e7af5495cee0493c7dcf6697200b85b5b91",
    Path("/opt/models/adaface.onnx"): "6b6a35772fb636cdd4fa86520c1a259d0c41472a76f70f802b351837a00d9870",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument(
        "--allow-cpu-execution",
        action="store_true",
        help="Validate the CUDA build and models without requiring a visible GPU.",
    )
    return result


def main() -> None:
    args = parser().parse_args()
    for path, expected in MODELS.items():
        if sha256(path) != expected:
            raise RuntimeError(f"model hash mismatch: {path.name}")

    available = ort.get_available_providers()
    if "CUDAExecutionProvider" not in available:
        raise RuntimeError("ONNX Runtime was not built with CUDAExecutionProvider")

    providers = (
        ["CPUExecutionProvider"]
        if args.allow_cpu_execution
        else ["CUDAExecutionProvider"]
    )
    detector = ort.InferenceSession(str(next(iter(MODELS))), providers=providers)
    embedder = ort.InferenceSession(
        "/opt/models/adaface.onnx", providers=providers
    )
    if not args.allow_cpu_execution:
        for name, session in (("detector", detector), ("embedder", embedder)):
            if session.get_providers()[0] != "CUDAExecutionProvider":
                raise RuntimeError(f"{name} did not initialize on CUDA")

    detector_input = detector.get_inputs()[0]
    detector_outputs = detector.run(
        None, {detector_input.name: np.zeros((1, 3, 640, 640), np.float32)}
    )
    embedding_input = embedder.get_inputs()[0]
    embedding = embedder.run(
        None, {embedding_input.name: np.zeros((1, 3, 112, 112), np.float32)}
    )[0].reshape(-1)
    if len(detector_outputs) != 9:
        raise RuntimeError(f"detector returned {len(detector_outputs)} outputs, expected 9")
    if len(embedding) != 512:
        raise RuntimeError(f"embedder returned {len(embedding)} dimensions, expected 512")

    mode = "CPU packaging check" if args.allow_cpu_execution else "CUDA execution"
    print(f"GPU image verification succeeded ({mode})")


if __name__ == "__main__":
    main()
