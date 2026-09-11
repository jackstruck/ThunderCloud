"""Generation-guarded maintenance artifacts in an execution-scoped GCS prefix."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath


class CloudArtifacts:
    def __init__(self, uri, *, client=None, resume=False):
        from google.cloud import storage

        if not uri.startswith("gs://"):
            raise ValueError("An execution-scoped gs:// artifact prefix is required")
        bucket, separator, prefix = uri[5:].partition("/")
        prefix = prefix.rstrip("/")
        if not bucket or not separator or not prefix or ".." in prefix.split("/"):
            raise ValueError("A bucket root is not an execution artifact prefix")
        self.bucket = (client or storage.Client()).bucket(bucket)
        self.prefix = prefix
        self.resume = resume
        self.generations = {}

    def write(self, name, data, content_type="application/json"):
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or str(path) != name:
            raise ValueError("Artifact name must stay within its execution prefix")
        key = f"{self.prefix}/{name}"
        if name not in self.generations:
            existing = self.bucket.get_blob(key) if self.resume else None
            self.generations[name] = int(existing.generation) if existing else 0
        blob = self.bucket.blob(key)
        blob.upload_from_string(
            data,
            content_type=content_type,
            if_generation_match=self.generations[name],
            timeout=60,
        )
        self.generations[name] = int(blob.generation)
        return {
            "object": key,
            "generation": int(blob.generation),
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
        }

    def progress(self, data):
        self.write("progress.json", json.dumps(data, sort_keys=True).encode())

    def finish(self, directory):
        directory = Path(directory)
        exported = []
        # Publish the final report last. Readers must not treat progress as completion.
        for path in sorted(directory.rglob("*")):
            if path.is_symlink():
                raise ValueError("Artifact directory must not contain symlinks")
            if (
                not path.is_file()
                or path.name.endswith(".tmp")
                or path == directory / "report.json"
            ):
                continue
            exported.append(
                self.write(
                    path.relative_to(directory).as_posix(),
                    path.read_bytes(),
                    "application/json"
                    if path.suffix == ".json"
                    else "application/octet-stream",
                )
            )
        self.write(
            "artifact-manifest.json",
            json.dumps({"format": 1, "artifacts": exported}, sort_keys=True).encode(),
        )
        self.write("report.json", (directory / "report.json").read_bytes())
