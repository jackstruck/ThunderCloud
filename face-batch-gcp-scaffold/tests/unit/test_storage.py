import base64

import pytest

from worker.storage import (
    GcsUri,
    decode_csek,
    has_customer_encryption,
    load_configured_csek,
    load_csek,
    validate_sha256,
)


def test_gcs_uri_validation():
    uri = GcsUri.parse("gs://bucket/videos/test.mp4")
    assert uri.bucket == "bucket"
    assert uri.object_name == "videos/test.mp4"
    with pytest.raises(ValueError):
        GcsUri.parse("https://bucket/videos/test.mp4")
    with pytest.raises(ValueError):
        GcsUri.parse("gs://bucket/videos/test.mp4?secret=x")


def test_load_csek_requires_strict_32_bytes(tmp_path):
    path = tmp_path / "key"
    path.write_bytes(base64.b64encode(b"x" * 32) + b"\n")
    assert load_csek(path) == b"x" * 32
    path.write_text("not base64!", encoding="ascii")
    with pytest.raises(ValueError, match="strict Base64"):
        load_csek(path)


def test_decode_and_configured_file_csek(tmp_path):
    encoded = base64.b64encode(b"y" * 32)
    assert decode_csek(encoded) == b"y" * 32
    path = tmp_path / "key"
    path.write_bytes(encoded)
    assert load_configured_csek("project", path, None) == b"y" * 32


def test_configured_csek_requires_a_source():
    with pytest.raises(ValueError, match="not configured"):
        load_configured_csek("project", None, None)


def test_sha256_validation():
    assert validate_sha256("A" * 64) == "a" * 64
    with pytest.raises(ValueError):
        validate_sha256("abc")


def test_customer_encryption_resource_detection():
    blob = type(
        "Blob",
        (),
        {"_properties": {"customerEncryption": {"encryptionAlgorithm": "AES256"}}},
    )()
    assert has_customer_encryption(blob)
