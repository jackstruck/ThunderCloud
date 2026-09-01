from __future__ import annotations

import base64
import binascii
import hashlib
import io
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class GcsUri:
    bucket: str
    object_name: str

    @classmethod
    def parse(cls, value: str) -> GcsUri:
        parsed = urlsplit(value)
        if parsed.scheme != "gs" or not parsed.netloc or not parsed.path.lstrip("/"):
            raise ValueError("expected a complete gs://bucket/object URI")
        if parsed.query or parsed.fragment:
            raise ValueError("GCS URI must not contain query or fragment data")
        return cls(parsed.netloc, parsed.path.lstrip("/"))

    def __str__(self) -> str:
        return f"gs://{self.bucket}/{self.object_name}"


def load_csek(path: Path) -> bytes:
    encoded = path.read_bytes().strip()
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("CSEK file is not strict Base64") from exc
    if len(raw) != 32:
        raise ValueError("CSEK must decode to exactly 32 bytes")
    return raw


def validate_sha256(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.lower()
    if not SHA256_RE.fullmatch(normalized):
        raise ValueError("SHA-256 must contain exactly 64 hexadecimal characters")
    return normalized


def has_customer_encryption(blob) -> bool:
    """Cloud Storage currently exposes customerEncryption in the JSON resource payload."""
    public_value = getattr(blob, "customer_encryption", None)
    properties = getattr(blob, "_properties", {})
    return bool(public_value or properties.get("customerEncryption"))


class HashingReader(io.RawIOBase):
    def __init__(self, source):
        self.source = source
        self.digest = hashlib.sha256()
        self.count = 0

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int:
        data = self.source.read(len(buffer))
        if not data:
            return 0
        buffer[: len(data)] = data
        self.digest.update(data)
        self.count += len(data)
        return len(data)


class StorageRepository:
    def __init__(
        self,
        project: str,
        bucket: str,
        source_prefix: str,
        staging_prefix: str,
        csek: bytes,
    ):
        from google.cloud import storage

        self.bucket_name = bucket
        self.source_prefix = source_prefix
        self.staging_prefix = staging_prefix
        self.csek = csek
        self.client = storage.Client(project=project)
        self.bucket = self.client.bucket(bucket)

    def _require_uri(self, value: str, prefix: str) -> GcsUri:
        uri = GcsUri.parse(value)
        if uri.bucket != self.bucket_name or not uri.object_name.startswith(prefix):
            raise ValueError(f"object must be under gs://{self.bucket_name}/{prefix}")
        return uri

    def source_uri(self, value: str) -> GcsUri:
        return self._require_uri(value, self.source_prefix)

    def staging_uri(self, value: str) -> GcsUri:
        return self._require_uri(value, self.staging_prefix)

    def stage(self, source_uri: str, metadata: dict[str, str]) -> tuple[str, int, int]:
        source = self.source_uri(source_uri)
        source_blob = self.bucket.blob(source.object_name, encryption_key=self.csek)
        source_blob.reload()
        destination_name = (
            f"{self.staging_prefix}{uuid.uuid4()}/{Path(source.object_name).name}"
        )
        destination = self.bucket.blob(destination_name, encryption_key=self.csek)

        token = None
        while True:
            token, _, _ = destination.rewrite(
                source_blob,
                token=token,
                if_generation_match=0,
                if_source_generation_match=int(source_blob.generation),
            )
            if token is None:
                break
        destination.reload()
        if not has_customer_encryption(destination):
            raise RuntimeError(
                "staged object does not report customer-supplied encryption"
            )
        if int(destination.size or -1) != int(source_blob.size or -2):
            raise RuntimeError("staged object size differs from source")
        destination.metadata = {
            str(k): str(v) for k, v in metadata.items() if v is not None
        }
        destination.patch(if_generation_match=int(destination.generation))
        return (
            f"gs://{self.bucket_name}/{destination_name}",
            int(destination.generation),
            int(destination.size),
        )

    def download_staging(
        self,
        staging_uri: str,
        generation: int | None = None,
        max_bytes: int | None = None,
    ) -> tuple[bytes, str]:
        uri = self.staging_uri(staging_uri)
        blob = self.bucket.blob(
            uri.object_name, generation=generation, encryption_key=self.csek
        )
        blob.reload()
        if not has_customer_encryption(blob):
            raise RuntimeError(
                "staging object does not report customer-supplied encryption"
            )
        if max_bytes is not None and int(blob.size or 0) > max_bytes:
            raise ValueError("staging object exceeds configured maximum video size")
        data = blob.download_as_bytes()
        return data, hashlib.sha256(data).hexdigest()

    def delete_staging(self, staging_uri: str, generation: int | None = None) -> None:
        uri = self.staging_uri(staging_uri)
        self.bucket.blob(
            uri.object_name, generation=generation, encryption_key=self.csek
        ).delete(if_generation_match=generation)
