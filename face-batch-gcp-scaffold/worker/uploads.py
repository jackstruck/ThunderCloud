from __future__ import annotations

from dataclasses import dataclass

from .storage import has_customer_encryption


@dataclass(frozen=True)
class UploadedObject:
    generation: int
    size: int


class UploadService:
    """CSEK-backed resumable uploads without exposing the encryption key."""

    def __init__(self, storage_repository, run_repository, origin: str):
        self.storage = storage_repository
        self.runs = run_repository
        self.origin = origin

    def create_session(self, run_id: str) -> str:
        upload = self.runs.prepare_upload(run_id)
        blob = self.storage.bucket.blob(
            upload["object_name"], encryption_key=self.storage.csek
        )
        return blob.create_resumable_upload_session(
            content_type=upload["content_type"],
            size=upload["expected_bytes"],
            origin=self.origin,
            if_generation_match=0,
            timeout=30,
        )

    def verify_completed(self, run_id: str) -> UploadedObject:
        upload = self.runs.prepare_upload(run_id)
        blob = self.storage.bucket.blob(
            upload["object_name"], encryption_key=self.storage.csek
        )
        blob.reload(timeout=30)
        if not has_customer_encryption(blob):
            raise RuntimeError(
                "uploaded object does not report customer-supplied encryption"
            )
        generation = int(blob.generation)
        size = int(blob.size)
        if size != upload["expected_bytes"]:
            raise ValueError("uploaded object size differs from declared upload size")
        return UploadedObject(generation, size)
