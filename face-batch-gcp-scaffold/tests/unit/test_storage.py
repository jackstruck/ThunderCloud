import base64
import hashlib

import pytest

from worker.storage import (
    GcsUri,
    StorageRepository,
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


def test_temporary_upload_is_csek_generation_guarded_and_verified():
    class Blob:
        generation = 9
        size = 8

        def __init__(self):
            self.customer_encryption = {"encryptionAlgorithm": "AES256"}

        def upload_from_file(self, stream, **kwargs):
            self.uploaded = stream.read()
            self.kwargs = kwargs

        def reload(self, **_kwargs):
            pass

    class Bucket:
        def __init__(self):
            self.value = Blob()

        def blob(self, name, **kwargs):
            self.call = (name, kwargs)
            return self.value

    repository = object.__new__(StorageRepository)
    repository.bucket = Bucket()
    repository.csek = b"k" * 32
    data = b"\x89PNG\r\n\x1a\n"
    result = repository.upload_temporary(
        "00000000-0000-4000-8000-000000000000",
        data,
        "image/png",
        hashlib.sha256(data).hexdigest(),
    )
    assert result == (
        "submissions-temporary/00000000-0000-4000-8000-000000000000/source.png",
        9,
        8,
    )
    assert repository.bucket.value.kwargs["if_generation_match"] == 0
    # GCS validates its supported CRC32C/MD5 transport checksum. The repository
    # independently validates the caller's SHA-256 before upload.
    assert repository.bucket.value.kwargs["checksum"] == "auto"
    assert repository.bucket.call[1] == {"encryption_key": b"k" * 32}


def test_retained_deletion_uses_recorded_bucket_and_generation():
    from unittest.mock import Mock

    repository = object.__new__(StorageRepository)
    repository.bucket_name = "recorded-bucket"
    repository.bucket = Mock()
    repository.csek = b"k" * 32
    with pytest.raises(ValueError, match="bucket differs"):
        repository.delete_retained("other-bucket", "training-media/source.mp4", 7)
    repository.bucket.blob.assert_not_called()
    repository.delete_retained("recorded-bucket", "training-media/source.mp4", 7)
    repository.bucket.blob.assert_called_once_with(
        "training-media/source.mp4", generation=7, encryption_key=repository.csek
    )
    repository.bucket.blob.return_value.delete.assert_called_once_with(
        if_generation_match=7, timeout=30
    )
