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

from google.api_core.exceptions import NotFound

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


def decode_csek(encoded: bytes) -> bytes:
    encoded = encoded.strip()
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("CSEK file is not strict Base64") from exc
    if len(raw) != 32:
        raise ValueError("CSEK must decode to exactly 32 bytes")
    return raw


def load_csek(path: Path) -> bytes:
    return decode_csek(path.read_bytes())


def load_csek_secret(project: str, secret_id: str) -> bytes:
    from google.cloud import secretmanager

    if not re.fullmatch(r"[A-Za-z0-9_-]+", secret_id):
        raise ValueError("Secret Manager secret ID contains unsupported characters")
    client = secretmanager.SecretManagerServiceClient()
    response = client.access_secret_version(
        request={"name": f"projects/{project}/secrets/{secret_id}/versions/latest"}
    )
    return decode_csek(response.payload.data)


def load_configured_csek(
    project: str, path: Path | None, secret_id: str | None
) -> bytes:
    if secret_id:
        return load_csek_secret(project, secret_id)
    if path is None:
        raise ValueError("CSEK source is not configured")
    return load_csek(path)


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

    def verify_source(self, source_uri: str) -> tuple[int, int]:
        uri = self.source_uri(source_uri)
        blob = self.bucket.blob(uri.object_name, encryption_key=self.csek)
        blob.reload()
        if not has_customer_encryption(blob):
            raise RuntimeError(
                "source object does not report customer-supplied encryption"
            )
        return int(blob.generation), int(blob.size)

    def stage(self, source_uri: str, metadata: dict[str, str]) -> tuple[str, int, int]:
        source = self.source_uri(source_uri)
        source_blob = self.bucket.blob(source.object_name, encryption_key=self.csek)
        source_blob.reload()
        if not has_customer_encryption(source_blob):
            raise RuntimeError(
                "source object does not report customer-supplied encryption"
            )
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

    def verify_staging(
        self, staging_uri: str, generation: int | None = None
    ) -> tuple[int, int]:
        """Return the immutable generation and size of an existing CSEK staging object."""
        uri = self.staging_uri(staging_uri)
        blob = self.bucket.blob(
            uri.object_name, generation=generation, encryption_key=self.csek
        )
        blob.reload()
        if not has_customer_encryption(blob):
            raise RuntimeError(
                "staging object does not report customer-supplied encryption"
            )
        return int(blob.generation), int(blob.size)

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

    def upload_temporary(
        self,
        run_id: str,
        data: bytes,
        content_type: str,
        sha256: str,
    ) -> tuple[str, int, int]:
        extension = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "video/mp4": ".mp4",
        }.get(content_type)
        if extension is None:
            raise ValueError("unsupported temporary media content type")
        if hashlib.sha256(data).hexdigest() != sha256:
            raise ValueError("temporary media checksum mismatch")
        uuid.UUID(run_id)
        object_name = f"submissions-temporary/{run_id}/source{extension}"
        blob = self.bucket.blob(object_name, encryption_key=self.csek)
        blob.upload_from_file(
            io.BytesIO(data),
            size=len(data),
            content_type=content_type,
            if_generation_match=0,
            checksum="auto",
            timeout=300,
        )
        blob.reload(timeout=30)
        if not has_customer_encryption(blob):
            raise RuntimeError(
                "temporary object does not report customer-supplied encryption"
            )
        if int(blob.size) != len(data):
            raise RuntimeError("temporary object size differs from fetched media")
        return object_name, int(blob.generation), int(blob.size)

    def delete_temporary(self, object_name: str, generation: int) -> None:
        if not object_name.startswith("submissions-temporary/"):
            raise ValueError(
                "cleanup object is outside the temporary submission prefix"
            )
        if generation <= 0:
            raise ValueError("cleanup generation must be positive")
        self.bucket.blob(
            object_name, generation=generation, encryption_key=self.csek
        ).delete(if_generation_match=generation, timeout=30)

    def delete_retained(self, object_name: str, generation: int) -> None:
        if not object_name.startswith("training-media/"):
            raise ValueError("retained object is outside the training-media prefix")
        self.bucket.blob(
            object_name, generation=generation, encryption_key=self.csek
        ).delete(if_generation_match=generation, timeout=30)

    def promote_temporary(
        self, source_id: str, object_name: str, generation: int
    ) -> tuple[str, int, int]:
        uuid.UUID(source_id)
        if not object_name.startswith("submissions-temporary/"):
            raise ValueError("promotion source is outside the temporary prefix")
        suffix = Path(object_name).suffix
        destination_name = f"training-media/{source_id}/source{suffix}"
        source = self.bucket.blob(
            object_name, generation=generation, encryption_key=self.csek
        )
        source.reload(timeout=30)
        if not has_customer_encryption(source):
            raise RuntimeError("promotion source is not CSEK encrypted")
        destination = self.bucket.blob(destination_name, encryption_key=self.csek)
        try:
            destination.reload(timeout=30)
        except NotFound:
            pass
        else:
            if (
                not has_customer_encryption(destination)
                or destination.size != source.size
            ):
                raise RuntimeError("existing retained source verification failed")
            return destination_name, int(destination.generation), int(destination.size)
        token = None
        while True:
            token, _, _ = destination.rewrite(
                source,
                token=token,
                if_generation_match=0,
                if_source_generation_match=generation,
            )
            if token is None:
                break
        destination.reload(timeout=30)
        if not has_customer_encryption(destination) or destination.size != source.size:
            raise RuntimeError("retained source verification failed")
        return destination_name, int(destination.generation), int(destination.size)

    def temporary_generation(self, object_name: str) -> int:
        if not object_name.startswith("submissions-temporary/"):
            raise ValueError("object is outside the temporary submission prefix")
        blob = self.bucket.blob(object_name, encryption_key=self.csek)
        blob.reload(timeout=30)
        if not has_customer_encryption(blob):
            raise RuntimeError(
                "temporary object does not report customer-supplied encryption"
            )
        return int(blob.generation)

    def download_temporary(
        self, object_name: str, generation: int, max_bytes: int
    ) -> tuple[bytes, str]:
        if not object_name.startswith("submissions-temporary/"):
            raise ValueError("media object is outside the temporary submission prefix")
        blob = self.bucket.blob(
            object_name, generation=generation, encryption_key=self.csek
        )
        blob.reload(timeout=30)
        if not has_customer_encryption(blob):
            raise RuntimeError(
                "temporary object does not report customer-supplied encryption"
            )
        if int(blob.size or 0) <= 0 or int(blob.size) > max_bytes:
            raise ValueError("temporary object exceeds configured byte limit")
        data = blob.download_as_bytes(
            if_generation_match=generation, timeout=300, checksum="auto"
        )
        return data, hashlib.sha256(data).hexdigest()

    def upload_preview(
        self, run_id: str, group_id: str, data: bytes
    ) -> tuple[str, int]:
        uuid.UUID(run_id)
        uuid.UUID(group_id)
        object_name = f"submissions-temporary/{run_id}/previews/{group_id}.jpg"
        blob = self.bucket.blob(object_name, encryption_key=self.csek)
        blob.upload_from_file(
            io.BytesIO(data),
            size=len(data),
            content_type="image/jpeg",
            if_generation_match=0,
            checksum="auto",
            timeout=60,
        )
        blob.reload(timeout=30)
        if not has_customer_encryption(blob):
            raise RuntimeError("preview does not report customer-supplied encryption")
        return object_name, int(blob.generation)

    def download_private_jpeg(self, object_name: str, generation: int) -> bytes:
        if not object_name.startswith(("submissions-temporary/", "subject-gallery/")):
            raise ValueError("image object is outside an allowed private prefix")
        blob = self.bucket.blob(
            object_name, generation=generation, encryption_key=self.csek
        )
        blob.reload(timeout=30)
        if not has_customer_encryption(blob):
            raise RuntimeError(
                "private image does not report customer-supplied encryption"
            )
        if blob.content_type != "image/jpeg" or int(blob.size or 0) > 5_000_000:
            raise ValueError("private image metadata is invalid")
        return blob.download_as_bytes(
            if_generation_match=generation, timeout=60, checksum="auto"
        )

    def download_source_generation(
        self, source_uri: str, generation: int, max_bytes: int
    ) -> bytes:
        uri = self.source_uri(source_uri)
        blob = self.bucket.blob(
            uri.object_name, generation=generation, encryption_key=self.csek
        )
        blob.reload(timeout=30)
        if not has_customer_encryption(blob):
            raise RuntimeError("source does not report customer-supplied encryption")
        if int(blob.size or 0) <= 0 or int(blob.size) > max_bytes:
            raise ValueError("source exceeds gallery backfill byte limit")
        return blob.download_as_bytes(
            if_generation_match=generation, timeout=900, checksum="auto"
        )

    def download_source_file(
        self, source_uri: str, generation: int, max_bytes: int, destination: Path
    ) -> tuple[int, str]:
        uri = self.source_uri(source_uri)
        blob = self.bucket.blob(
            uri.object_name, generation=generation, encryption_key=self.csek
        )
        blob.reload(timeout=30)
        size = int(blob.size or 0)
        if not has_customer_encryption(blob):
            raise RuntimeError("source does not report customer-supplied encryption")
        if size <= 0 or size > max_bytes:
            raise ValueError("source exceeds gallery backfill byte limit")
        digest = hashlib.sha256()
        with (
            destination.open("wb") as output,
            blob.open("rb", if_generation_match=generation) as reader,
        ):
            while block := reader.read(8 * 1024 * 1024):
                output.write(block)
                digest.update(block)
        destination.chmod(0o600)
        return size, digest.hexdigest()

    def upload_gallery_face(
        self, subject_id: str, representative_id: str, data: bytes
    ) -> tuple[str, int]:
        uuid.UUID(subject_id)
        uuid.UUID(representative_id)
        if not data.startswith(b"\xff\xd8\xff") or len(data) > 5_000_000:
            raise ValueError("representative face must be a bounded JPEG")
        object_name = f"subject-gallery/{subject_id}/{representative_id}.jpg"
        blob = self.bucket.blob(object_name, encryption_key=self.csek)
        try:
            blob.reload(timeout=30)
        except NotFound:
            pass
        else:
            if not has_customer_encryption(blob) or blob.content_type != "image/jpeg":
                raise RuntimeError("existing gallery object verification failed")
            return object_name, int(blob.generation)
        blob.upload_from_file(
            io.BytesIO(data),
            size=len(data),
            content_type="image/jpeg",
            if_generation_match=0,
            checksum="auto",
            timeout=60,
        )
        blob.reload(timeout=30)
        if not has_customer_encryption(blob):
            raise RuntimeError(
                "gallery object does not report customer-supplied encryption"
            )
        return object_name, int(blob.generation)

    def delete_gallery_face(self, object_name: str, generation: int) -> None:
        if not object_name.startswith("subject-gallery/"):
            raise ValueError("object is outside the gallery prefix")
        self.bucket.blob(
            object_name, generation=generation, encryption_key=self.csek
        ).delete(if_generation_match=generation, timeout=30)
