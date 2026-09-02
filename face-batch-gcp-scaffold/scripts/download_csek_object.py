#!/usr/bin/env python3
"""Download one configured CSEK-encrypted source object to a private local file."""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from worker.storage import GcsUri, has_customer_encryption, load_csek


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download and locally decrypt one CSEK-encrypted GCS source object"
    )
    parser.add_argument("gcs_uri", help="gs:// URL beneath the configured source prefix")
    parser.add_argument(
        "--output",
        type=Path,
        help="new local output path (default: the object's basename in this directory)",
    )
    parser.add_argument(
        "--generation",
        type=int,
        help="require this exact object generation (default: pin the current generation)",
    )
    return parser.parse_args()


def main() -> int:
    args = arguments()
    project = os.getenv("FACE_PROJECT_ID", "teak-banner-dome")
    bucket_name = os.getenv("FACE_BUCKET", "teak-banner-dome-bulk-videos")
    source_prefix = os.getenv("FACE_SOURCE_PREFIX", "videos/")
    csek_path = Path(
        os.getenv("FACE_CSEK_FILE", "/workspaces/ThunderCloud/.secrets/gcs-csek.base64")
    )
    max_bytes = int(os.getenv("FACE_MAX_VIDEO_BYTES", "10737418240"))

    uri = GcsUri.parse(args.gcs_uri)
    if uri.bucket != bucket_name or not uri.object_name.startswith(source_prefix):
        raise ValueError(f"object must be under gs://{bucket_name}/{source_prefix}")
    if args.generation is not None and args.generation <= 0:
        raise ValueError("--generation must be positive")
    if csek_path.stat().st_mode & 0o077:
        raise PermissionError("FACE_CSEK_FILE must not be accessible by group or others")

    output = (args.output or Path(uri.object_name).name).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    csek = load_csek(csek_path)

    from google.cloud import storage

    client = storage.Client(project=project)
    blob = client.bucket(bucket_name).blob(uri.object_name, encryption_key=csek)
    blob.reload()
    if not has_customer_encryption(blob):
        raise RuntimeError("source object does not report customer-supplied encryption")
    generation = int(blob.generation)
    if args.generation is not None and generation != args.generation:
        raise RuntimeError(
            f"generation mismatch: requested {args.generation}, current object is {generation}"
        )
    size = int(blob.size or 0)
    if size <= 0:
        raise RuntimeError("source object is empty")
    if size > max_bytes:
        raise RuntimeError(f"source object exceeds FACE_MAX_VIDEO_BYTES ({max_bytes})")

    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            blob.download_to_file(
                handle,
                if_generation_match=generation,
                timeout=300,
            )
            handle.flush()
            os.fsync(handle.fileno())
        if output.stat().st_size != size:
            raise RuntimeError("downloaded size does not match GCS metadata")
        digest = hashlib.sha256()
        with output.open("rb") as handle:
            for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
                digest.update(chunk)
    except BaseException:
        output.unlink(missing_ok=True)
        raise

    print(f"downloaded={output}")
    print(f"generation={generation}")
    print(f"bytes={size}")
    print(f"sha256={digest.hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
