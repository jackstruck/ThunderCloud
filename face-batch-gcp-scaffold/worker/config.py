from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"required environment variable is missing: {name}")
    return value


@dataclass(frozen=True)
class Settings:
    project_id: str
    bucket: str
    source_prefix: str
    staging_prefix: str
    csek_file: Path | None
    csek_secret: str | None
    cloud_sql_instance: str
    db_user: str
    db_name: str = "face_index"
    cloud_sql_ip_type: str = "PUBLIC"
    detector_model: Path = Path("models/scrfd.onnx")
    embedding_model: Path = Path("models/adaface.onnx")
    face_output_dir: Path | None = None
    embedding_color_order: str = "RGB"
    detector_fps: float = 8.0
    max_video_bytes: int = 10_737_418_240
    best_n: int = 5
    top_k: int = 5
    match_threshold: float = 0.55
    matching_enabled: bool = False
    require_cuda: bool = False
    threshold_version: str = "unvalidated-v1"
    detector_version: str = "scrfd-onnx"
    embedding_model_version: str = "adaface-onnx"
    worker_version: str = "0.2.0"

    @classmethod
    def from_env(cls) -> Settings:
        csek_secret = os.getenv("FACE_CSEK_SECRET")
        csek_file = os.getenv("FACE_CSEK_FILE")
        return cls(
            project_id=os.getenv("FACE_PROJECT_ID", "teak-banner-dome"),
            bucket=os.getenv("FACE_BUCKET", "teak-banner-dome-bulk-videos"),
            source_prefix=os.getenv("FACE_SOURCE_PREFIX", "videos/"),
            staging_prefix=os.getenv("FACE_STAGING_PREFIX", "face-staging/"),
            csek_file=(
                Path(csek_file or "/run/secrets/gcs-csek.base64")
                if not csek_secret
                else None
            ),
            csek_secret=csek_secret,
            cloud_sql_instance=_required("FACE_CLOUD_SQL_INSTANCE"),
            db_user=_required("FACE_DB_USER"),
            db_name=os.getenv("FACE_DB_NAME", "face_index"),
            cloud_sql_ip_type=os.getenv("FACE_CLOUD_SQL_IP_TYPE", "PUBLIC").upper(),
            detector_model=Path(os.getenv("FACE_DETECTOR_MODEL", "models/scrfd.onnx")),
            embedding_model=Path(
                os.getenv("FACE_EMBEDDING_MODEL", "models/adaface.onnx")
            ),
            face_output_dir=(
                Path(value) if (value := os.getenv("FACE_OUTPUT_DIR")) else None
            ),
            embedding_color_order=os.getenv("FACE_EMBEDDING_COLOR_ORDER", "RGB").upper(),
            detector_fps=float(os.getenv("FACE_DETECTOR_FPS", "8")),
            max_video_bytes=int(os.getenv("FACE_MAX_VIDEO_BYTES", "10737418240")),
            best_n=int(os.getenv("FACE_BEST_N", "5")),
            top_k=int(os.getenv("FACE_TOP_K", "5")),
            match_threshold=float(os.getenv("FACE_MATCH_THRESHOLD", "0.55")),
            matching_enabled=os.getenv("FACE_MATCHING_ENABLED", "false").lower()
            in {"1", "true", "yes"},
            require_cuda=os.getenv("FACE_REQUIRE_CUDA", "false").lower()
            in {"1", "true", "yes"},
            threshold_version=os.getenv("FACE_THRESHOLD_VERSION", "unvalidated-v1"),
            detector_version=os.getenv("FACE_DETECTOR_VERSION", "scrfd-onnx"),
            embedding_model_version=os.getenv(
                "FACE_EMBEDDING_MODEL_VERSION", "adaface-onnx"
            ),
            worker_version=os.getenv("FACE_WORKER_VERSION", "0.2.0"),
        )

    def validate(self) -> None:
        if (self.csek_file is None) == (self.csek_secret is None):
            raise ValueError("configure exactly one of FACE_CSEK_FILE or FACE_CSEK_SECRET")
        if not self.source_prefix.endswith("/") or not self.staging_prefix.endswith(
            "/"
        ):
            raise ValueError("source and staging prefixes must end with /")
        if self.source_prefix == self.staging_prefix:
            raise ValueError("source and staging prefixes must differ")
        if (
            self.detector_fps <= 0
            or self.max_video_bytes <= 0
            or self.best_n <= 0
            or self.top_k <= 0
        ):
            raise ValueError(
                "detector_fps, max_video_bytes, best_n, and top_k must be positive"
            )
        if not 0 <= self.match_threshold <= 1:
            raise ValueError("match threshold must be between 0 and 1")
        if self.embedding_color_order not in {"RGB", "BGR"}:
            raise ValueError("embedding color order must be RGB or BGR")
        if self.cloud_sql_ip_type not in {"PUBLIC", "PRIVATE"}:
            raise ValueError("Cloud SQL IP type must be PUBLIC or PRIVATE")
