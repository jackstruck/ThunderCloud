FROM nvidia/cuda:13.0.1-cudnn-runtime-ubuntu24.04 AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PATH=/opt/venv/bin:$PATH

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ca-certificates ffmpeg libglib2.0-0 libgl1 python3 python3-pip python3-venv zlib1g \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY worker ./worker
RUN python3 -m venv /opt/venv \
    && python -m pip install --upgrade pip \
    && python -m pip install . \
    && python -m pip uninstall --yes onnxruntime \
    && python -m pip install onnxruntime-gpu==1.29.0

COPY models/scrfd.onnx models/adaface.onnx models/SHA256SUMS /opt/models/
RUN cd /opt/models && sha256sum --check SHA256SUMS

COPY scripts/verify_gpu_image.py /app/scripts/verify_gpu_image.py

ENV FACE_DETECTOR_MODEL=/opt/models/scrfd.onnx \
    FACE_EMBEDDING_MODEL=/opt/models/adaface.onnx \
    FACE_EMBEDDING_COLOR_ORDER=BGR \
    FACE_DETECTOR_VERSION=scrfd-10g-5838f7fe \
    FACE_EMBEDDING_MODEL_VERSION=adaface-ir18-6b6a3577 \
    FACE_WORKER_VERSION=0.2.0-cloud-run-r4

# Validate hashes, model contracts, and that this is a CUDA-enabled ORT build. Actual
# CUDA execution is verified on the L4 with the strict form of the same command.
RUN python /app/scripts/verify_gpu_image.py --allow-cpu-execution

RUN useradd --create-home --uid 10001 worker
USER worker

ENTRYPOINT ["python", "-m", "worker"]
