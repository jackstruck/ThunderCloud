from pathlib import Path

import pytest

from worker.cloud_run_drain import classify_failure, drain
from worker.config import Settings


def settings(**overrides):
    values = {
        "project_id": "p",
        "bucket": "bucket",
        "source_prefix": "videos/",
        "staging_prefix": "face-staging/",
        "csek_file": None,
        "csek_secret": "face-batch-gcs-csek",
        "cloud_sql_instance": "p:r:i",
        "db_user": "runtime@p.iam",
        "cloud_sql_ip_type": "PRIVATE",
        "detector_model": Path("detector"),
        "embedding_model": Path("embedder"),
        "matching_enabled": True,
        "require_cuda": True,
    }
    values.update(overrides)
    return Settings(**values)


def test_cloud_run_invariants_fail_before_credentials_or_models():
    rollout = "4a0e791d-fc5e-44ae-922f-9b71d91dca90"
    with pytest.raises(RuntimeError, match="MATCHING_ENABLED"):
        drain(rollout, settings(matching_enabled=False))
    with pytest.raises(RuntimeError, match="private Cloud SQL"):
        drain(rollout, settings(cloud_sql_ip_type="PUBLIC"))
    with pytest.raises(RuntimeError, match="Secret Manager"):
        drain(rollout, settings(csek_file=Path("key"), csek_secret=None))
    with pytest.raises(RuntimeError, match="REQUIRE_CUDA"):
        drain(rollout, settings(require_cuda=False))


def test_failure_classification_is_sanitized_and_bounded():
    assert classify_failure(ValueError("bad media")) == ("INVALID_INPUT", False)
    assert classify_failure(RuntimeError("SHA-256 mismatch")) == (
        "CHECKSUM_MISMATCH",
        False,
    )
    assert classify_failure(RuntimeError("connection temporarily unavailable")) == (
        "TRANSIENT_RUNTIME",
        True,
    )
    code, retryable = classify_failure(Exception("private details"))
    assert code == "UNKNOWN_FAILURE"
    assert retryable
