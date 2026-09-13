#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from bulk_download.config import load_config, load_csek
from bulk_download.download import download_video
from bulk_download.fetch import FetchError, Fetcher
from bulk_download.manifest import append, completed_hashes, completed_urls
from bulk_download.pipeline import discover_luluvid, load_inputs
from bulk_download.storage import StorageAdapter
from bulk_download.urls import luluvid_fetch_url


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@contextmanager
def content_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download and CSEK-upload direct Luluvid inputs")
    parser.add_argument("--config", default="./config.toml")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--delay", type=float, default=1.0)
    parser.add_argument("--partition", choices=("all", "front", "back"), default="all")
    parser.add_argument("--reverse", action="store_true")
    parser.add_argument("--error-code", action="append", help="Retry latest matching errors; repeat for multiple codes")
    return parser.parse_args()


def recover_existing(blob, config, uid: str) -> dict[str, object]:
    """Download an existing CSEK object once to recover ingestion-grade provenance."""
    size = int(blob.size or 0)
    content_type = (blob.content_type or "").split(";", 1)[0].strip().lower()
    if size <= 0 or size > config.http.max_video_bytes:
        raise RuntimeError("video_too_large" if size > config.http.max_video_bytes else "verification_failure")
    if content_type not in config.allowed_content_types:
        raise RuntimeError("unsupported_video_type")

    config.temp_dir.mkdir(parents=True, exist_ok=True)
    path = config.temp_dir / f"{uid}.recovery.part"
    digest = hashlib.sha256()
    downloaded_size = 0
    try:
        with path.open("xb") as handle:
            blob.download_to_file(
                handle,
                if_generation_match=int(blob.generation),
                timeout=300,
            )
            handle.flush()
            os.fsync(handle.fileno())
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(config.chunk_bytes), b""):
                downloaded_size += len(chunk)
                digest.update(chunk)
        if downloaded_size != size:
            raise RuntimeError("verification_failure")
        return {
            "sha256": digest.hexdigest(),
            "bytes": downloaded_size,
            "generation": int(blob.generation),
            "content_type": content_type,
        }
    finally:
        path.unlink(missing_ok=True)


def main(*, checkpoint=None) -> int:
    args = arguments()
    config = load_config(args.config)
    def record(item):
        append(config.manifest_file, item)
        if checkpoint is not None:
            checkpoint()

    key = load_csek(config)
    storage = StorageAdapter(config, key)
    storage.check_bucket()
    policy = getattr(storage.bucket, "soft_delete_policy", None)
    retention = int(getattr(policy, "retention_duration_seconds", 0) or 0)
    if retention != 0:
        print(f"refusing batch: bucket soft-delete retention is {retention} seconds", file=sys.stderr)
        return 2
    inputs = load_inputs(config.luluvid_file, "luluvid")
    midpoint = (len(inputs) + 1) // 2
    selected = {
        "all": inputs,
        "front": inputs[:midpoint],
        "back": inputs[midpoint:],
    }[args.partition]
    if args.reverse:
        selected = list(reversed(selected))
    done = completed_urls(config.manifest_file)
    hash_index = completed_hashes(config.manifest_file)
    pending = [url for url in selected if url not in done]
    if args.error_code:
        latest = {}
        if config.manifest_file.exists():
            for line in config.manifest_file.read_text().splitlines():
                if line.strip():
                    row = json.loads(line)
                    latest[row.get("luluvid_url")] = row
        pending = [url for url in pending if latest.get(url, {}).get("error_code") in args.error_code]
    if args.limit is not None:
        pending = pending[:args.limit]
    failures = 0
    print(
        f"inputs={len(inputs)} partition={args.partition} reverse={args.reverse} "
        f"completed={len(done)} pending_this_run={len(pending)}",
        flush=True,
    )
    with Fetcher(config.http) as fetcher:
        for index, luluvid_url in enumerate(pending, 1):
            downloaded = None
            try:
                found, missed = discover_luluvid(fetcher, luluvid_url)
                if missed or not found:
                    raise RuntimeError(missed[0].code if missed else "no_static_video_url")
                item = found[0]
                object_name = f"{config.gcp.prefix}/{item.uid}.mp4"
                existing = storage.existing(object_name)
                if existing is not None:
                    provenance = recover_existing(existing, config, item.uid)
                    record({
                        "timestamp": now(), "status": "complete", "recovered_from_gcs": True,
                        "luluvid_url": item.luluvid_url, "uid": item.uid,
                        "object": f"gs://{config.gcp.bucket}/{object_name}",
                        **provenance,
                    })
                    print(f"{index}/{len(pending)} recovered uid={item.uid}", flush=True)
                    continue
                with fetcher.referring_to(item.media_referer or luluvid_fetch_url(item.luluvid_url)):
                    downloaded = download_video(fetcher, config, item.media_url, item.uid)
                with content_lock(config.manifest_file.parent / "content-dedupe.lock"):
                    hash_index.update(completed_hashes(config.manifest_file))
                    prior = hash_index.get(downloaded.sha256)
                    if prior is not None:
                        record({
                            "timestamp": now(), "status": "duplicate",
                            "luluvid_url": item.luluvid_url, "uid": item.uid,
                            "sha256": downloaded.sha256,
                            "duplicate_of": prior["object"],
                            "duplicate_of_uid": prior.get("uid"),
                            "bytes": downloaded.size,
                        })
                    else:
                        object_name = f"{config.gcp.prefix}/{item.uid}{downloaded.extension}"
                        blob = storage.upload(
                            object_name, downloaded.path, downloaded.content_type, downloaded.size
                        )
                        completion_record = {
                            "timestamp": now(), "status": "complete",
                            "luluvid_url": item.luluvid_url, "uid": item.uid,
                            "object": f"gs://{config.gcp.bucket}/{object_name}",
                            "generation": int(blob.generation),
                            "content_type": downloaded.content_type,
                            "bytes": downloaded.size, "sha256": downloaded.sha256,
                        }
                        record(completion_record)
                        hash_index[downloaded.sha256] = completion_record
                downloaded.path.unlink(missing_ok=True)
                if prior is not None:
                    print(
                        f"{index}/{len(pending)} duplicate uid={item.uid} of={prior['object']}",
                        flush=True,
                    )
                else:
                    print(
                        f"{index}/{len(pending)} complete uid={item.uid} bytes={downloaded.size}",
                        flush=True,
                    )
            except KeyboardInterrupt:
                print("interrupted; completed uploads are saved", file=sys.stderr, flush=True)
                return 130
            except Exception as exc:
                failures += 1
                if downloaded is not None:
                    downloaded.path.unlink(missing_ok=True)
                record({
                    "timestamp": now(), "status": "failed", "luluvid_url": luluvid_url,
                    "error_code": str(exc) if str(exc) else type(exc).__name__,
                    "message": type(exc).__name__,
                })
                print(
                    f"{index}/{len(pending)} failed {type(exc).__name__}: {exc}",
                    file=sys.stderr,
                    flush=True,
                )
                if isinstance(exc, FetchError) and exc.code == "access_challenge":
                    print(
                        "access challenge reached; stopping with pending URLs and saved progress intact",
                        file=sys.stderr,
                        flush=True,
                    )
                    return 75
            time.sleep(max(0, args.delay))
    print(f"batch finished successes={len(pending) - failures} failures={failures}", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
