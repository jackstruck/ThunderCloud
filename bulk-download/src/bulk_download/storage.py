from __future__ import annotations

import base64
import hashlib
from pathlib import Path

import google_crc32c
from google.api_core.exceptions import NotFound, PreconditionFailed
from google.cloud import storage

from .config import Config
from .download import Downloaded, media_fingerprint
from .verification import VerificationError


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
        checks = {
            'size': blob.size == expected_size,
            'content_type': blob.content_type == content_type,
            'checksum': blob.crc32c == base64.b64encode(checksum.digest()).decode("ascii"),
            'encryption_algorithm': encryption.get("encryptionAlgorithm") == "AES256",
            'encryption_key': encryption.get("keySha256") == base64.b64encode(hashlib.sha256(self.csek).digest()).decode("ascii"),
        }
        failed = [name for name, passed in checks.items() if not passed]
        if failed:
            raise VerificationError('stored_object', 'Stored object differs: ' + ', '.join(failed))

    def reuse_existing(self, blob, downloaded: Downloaded, *, allow_remux: bool = False) -> Downloaded:
        try:
            self.verify(blob, downloaded.path, downloaded.content_type, downloaded.size)
            return downloaded
        except VerificationError:
            if not allow_remux:
                raise
        # Download the exact immutable generation and verify its bytes, media type
        # and encryption before considering equivalence. Never overwrite archives.
        existing = downloaded.path.with_suffix('.existing.mp4')
        try:
            blob.download_to_filename(str(existing), if_generation_match=int(blob.generation), checksum='crc32c')
            self.verify(blob, existing, downloaded.content_type, existing.stat().st_size)
            if media_fingerprint(existing) != media_fingerprint(downloaded.path):
                raise VerificationError('stored_media_mismatch', 'Stored and downloaded media packets or timing differ')
            digest = hashlib.sha256()
            with existing.open('rb') as handle:
                for chunk in iter(lambda: handle.read(self.config.chunk_bytes), b''):
                    digest.update(chunk)
            # Handoff must describe the archived bytes, not the new remux bytes.
            return Downloaded(downloaded.path, downloaded.content_type, downloaded.extension,
                              existing.stat().st_size, digest.hexdigest())
        finally:
            existing.unlink(missing_ok=True)

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
