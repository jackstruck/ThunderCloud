from __future__ import annotations

import base64
import hashlib
from pathlib import Path

import google_crc32c
from google.api_core.exceptions import NotFound, PreconditionFailed
from google.cloud import storage

from .config import Config


class StorageAdapter:
    def __init__(self, config: Config, csek: bytes):
        self.config = config
        self.csek = csek
        self.client = storage.Client(project=config.gcp.project)
        self.bucket = self.client.bucket(config.gcp.bucket)

    def check_bucket(self) -> None:
        self.bucket.reload()

    def _blob(self, object_name: str) -> storage.Blob:
        return self.bucket.blob(object_name, encryption_key=self.csek)

    def existing(self, object_name: str) -> storage.Blob | None:
        blob = self._blob(object_name)
        try:
            blob.reload()
            return blob
        except NotFound:
            return None

    def verify(self, blob, path: Path, content_type: str, expected_size: int) -> None:
        checksum = google_crc32c.Checksum()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(self.config.chunk_bytes), b""):
                checksum.update(chunk)
        encryption = blob._properties.get("customerEncryption", {})
        if (
            blob.size != expected_size
            or blob.content_type != content_type
            or blob.crc32c != base64.b64encode(checksum.digest()).decode("ascii")
            or encryption.get("encryptionAlgorithm") != "AES256"
            or encryption.get("keySha256") != base64.b64encode(hashlib.sha256(self.csek).digest()).decode("ascii")
        ):
            raise RuntimeError("verification_failure")

    def upload(self, object_name: str, path: Path, content_type: str, expected_size: int) -> storage.Blob:
        blob = self._blob(object_name)
        try:
            blob.upload_from_filename(
                str(path), content_type=content_type, if_generation_match=0, timeout=300
            )
        except PreconditionFailed as exc:
            raise RuntimeError("object_exists") from exc
        blob.reload()
        self.verify(blob, path, content_type, expected_size)
        return blob
