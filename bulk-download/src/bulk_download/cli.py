from __future__ import annotations

import argparse
import json
import sys
from contextlib import nullcontext
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .config import ConfigError, load_config, load_csek
from .download import download_video
from .fetch import Fetcher, FetchError
from .hotscope import HOSTS, discover_users, read_list, user_name, video_id
from .manifest import append, completed_hashes, completed_urls
from .pipeline import (
    DiscoveryFailure,
    discover,
    discover_justpaste,
    discover_luluvid,
    load_inputs,
)
from .storage import StorageAdapter
from .urls import UnsafeUrl


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _redact(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _log(stage: str, message: str, uid: str | None = None) -> None:
    suffix = f" uid={uid}" if uid else ""
    print(f"[{stage}]{suffix} {message}", file=sys.stderr)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bulk-download")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("doctor", "discover", "run"):
        child = subparsers.add_parser(command)
        child.add_argument("--config", default="./config.toml")
        if command in {"discover", "run"}:
            child.add_argument("--provider", choices=("legacy", "hotscope"), default="legacy")
            child.add_argument("--user", action="append", default=[], help="Hotscope username or profile URL; repeatable")
            child.add_argument("--users-file", help="one username or profile URL per line")
            child.add_argument("--video-id", action="append", default=[], help="select a video ID or page URL; repeatable")
            child.add_argument("--videos-file", help="selected video IDs or page URLs, one per line")
            child.add_argument("--max-videos-per-user", type=int, help="take the first N videos in provider order")
            child.add_argument("--max-pages", type=int, default=100, help="maximum listing pages per user")
            child.add_argument(
                "--input-stage", choices=("all", "heylink", "justpaste", "luluvid"), default="all"
            )
    return parser


def _discover_all(config, input_stage: str = "all", options=None, done=None) -> tuple[list, list]:
    if options is not None and options.provider == "hotscope":
        users = list(dict.fromkeys(user_name(v) for v in options.user + read_list(options.users_file)))
        selected = {video_id(v) for v in options.video_id + read_list(options.videos_file)}
        with Fetcher(config.http) as fetcher:
            return discover_users(fetcher, users, selected, options.max_videos_per_user, options.max_pages, done)
    successes, failures = [], []
    heylink_inputs = load_inputs(config.input_file, "heylink") if input_stage in {"all", "heylink"} else []
    justpaste_inputs = load_inputs(config.justpaste_file, "justpaste") if input_stage in {"all", "justpaste"} else []
    luluvid_inputs = load_inputs(config.luluvid_file, "luluvid") if input_stage in {"all", "luluvid"} else []
    with Fetcher(config.http) as fetcher:
        for entry in heylink_inputs if input_stage in {"all", "heylink"} else []:
            _log("discover", _redact(entry))
            try:
                found, missed = discover(fetcher, entry)
                successes.extend(found)
                failures.extend(missed)
            except (FetchError, UnsafeUrl) as exc:
                code = getattr(exc, "code", "unsafe_url")
                failures.append(DiscoveryFailure(code, str(exc), {"heylink_url": entry}))
        for entry in justpaste_inputs if input_stage in {"all", "justpaste"} else []:
            _log("discover", _redact(entry))
            try:
                found, missed = discover_justpaste(fetcher, entry)
                successes.extend(found)
                failures.extend(missed)
            except (FetchError, UnsafeUrl) as exc:
                code = getattr(exc, "code", "unsafe_url")
                failures.append(DiscoveryFailure(code, str(exc), {"justpaste_url": entry}))
        for entry in luluvid_inputs if input_stage in {"all", "luluvid"} else []:
            _log("discover", _redact(entry))
            try:
                found, missed = discover_luluvid(fetcher, entry)
                successes.extend(found)
                failures.extend(missed)
            except (FetchError, UnsafeUrl) as exc:
                code = getattr(exc, "code", "unsafe_url")
                failures.append(DiscoveryFailure(code, str(exc), {"luluvid_url": entry}))
    deduplicated = {item.luluvid_url: item for item in successes}
    return list(deduplicated.values()), failures


def doctor(config) -> int:
    key = load_csek(config)
    for input_file in (config.input_file, config.justpaste_file, config.luluvid_file):
        if not input_file.is_file():
            raise ConfigError(f"input file does not exist: {input_file}")
    config.temp_dir.mkdir(parents=True, exist_ok=True)
    config.manifest_file.parent.mkdir(parents=True, exist_ok=True)
    adapter = StorageAdapter(config, key)
    adapter.check_bucket()
    _log("doctor", f"configuration, CSEK, ADC, and bucket are accessible: {config.gcp.bucket}")
    return 0


def print_discovery(config, input_stage: str = "all", options=None) -> int:
    found, failures = _discover_all(config, input_stage, options)
    for item in found:
        output = asdict(item)
        output["media_url"] = _redact(item.media_url)
        print(json.dumps(output, sort_keys=True))
    for failure in failures:
        _log(failure.code, str(failure))
    return 1 if failures else 0


def _provenance(item) -> dict:
    if getattr(item, "provider", None) == "hotscope":
        return {key: value for key, value in asdict(item).items()
                if key not in {"media_url", "media_referer"}}
    return {"heylink_url": item.heylink_url, "justpaste_url": item.justpaste_url,
            "luluvid_url": item.luluvid_url, "uid": item.uid}


def run(config, input_stage: str = "all", options=None) -> int:
    key = load_csek(config)
    stale = sorted([*config.temp_dir.glob("*.part"), *config.temp_dir.glob("*.segments.ts")]) if config.temp_dir.exists() else []
    if stale:
        raise ConfigError(f"stale partial files must be removed manually: {', '.join(map(str, stale))}")
    done = completed_urls(config.manifest_file)
    hash_index = completed_hashes(config.manifest_file)
    storage = StorageAdapter(config, key)
    storage.check_bucket()
    found, failures = _discover_all(config, input_stage, options, done)
    for failure in failures:
        append(config.manifest_file, {
            "timestamp": _timestamp(), "status": "failed", "error_code": failure.code,
            "message": str(failure), **failure.context,
        })
    item_failed = False
    with Fetcher(config.http) as fetcher:
        for item in found:
            if item.page_url in done:
                _log("skip", "manifest already records completion", item.uid)
                continue
            downloaded = None
            try:
                restriction = fetcher.restricted_to(HOSTS) if getattr(item, "provider", None) == "hotscope" else nullcontext()
                with restriction, fetcher.referring_to(item.media_referer or item.page_url):
                    downloaded = download_video(fetcher, config, item.media_url, item.uid)
                prior = hash_index.get(downloaded.sha256)
                if prior is not None:
                    append(config.manifest_file, {
                        "timestamp": _timestamp(), "status": "duplicate",
                        **_provenance(item),
                        "sha256": downloaded.sha256, "bytes": downloaded.size,
                        "duplicate_of": prior["object"],
                        "duplicate_of_uid": prior.get("uid"),
                    })
                    downloaded.path.unlink(missing_ok=True)
                    _log("duplicate", f"matches {prior['object']}", item.uid)
                    continue
                object_name = f"{config.gcp.prefix}/{item.uid}{downloaded.extension}"
                existing = storage.existing(object_name)
                if existing is not None:
                    storage.verify(existing, downloaded.path, downloaded.content_type, downloaded.size)
                    record = {
                        "timestamp": _timestamp(), "status": "complete", "recovered_from_gcs": True,
                        **_provenance(item),
                        "object": f"gs://{config.gcp.bucket}/{object_name}", "generation": int(existing.generation),
                        "sha256": downloaded.sha256, "bytes": downloaded.size,
                        "content_type": downloaded.content_type,
                    }
                    append(config.manifest_file, record)
                    hash_index[downloaded.sha256] = record
                    downloaded.path.unlink(missing_ok=True)
                    continue
                blob = storage.upload(object_name, downloaded.path, downloaded.content_type, downloaded.size)
                record = {
                    "timestamp": _timestamp(), "status": "complete", **_provenance(item), "object": f"gs://{config.gcp.bucket}/{object_name}",
                    "generation": int(blob.generation), "content_type": downloaded.content_type,
                    "bytes": downloaded.size, "sha256": downloaded.sha256,
                }
                append(config.manifest_file, record)
                hash_index[downloaded.sha256] = record
                downloaded.path.unlink(missing_ok=True)
                _log("complete", record["object"], item.uid)
            except Exception as exc:  # noqa: BLE001 - record per-item failures and continue the batch
                item_failed = True
                if downloaded is not None:
                    downloaded.path.unlink(missing_ok=True)
                code = getattr(exc, "code", None) or (str(exc) if str(exc) in {
                    "unsupported_video_type", "video_too_large", "download_failure", "object_exists",
                    "verification_failure", "insufficient_disk_space",
                } else "upload_failure")
                append(config.manifest_file, {
                    "timestamp": _timestamp(), "status": "failed", "error_code": code,
                    "message": type(exc).__name__, **_provenance(item),
                })
                _log(code, type(exc).__name__, item.uid)
    return 1 if failures or item_failed else 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        config = load_config(Path(args.config))
        if args.command == "doctor":
            return doctor(config)
        if args.user or args.users_file:
            args.provider = "hotscope"
        if args.provider == "hotscope":
            if not (args.user or read_list(args.users_file)):
                raise ConfigError("Hotscope requires --user or --users-file")
            if args.input_stage != "all":
                raise ConfigError("--input-stage applies only to legacy inputs")
            if args.max_pages <= 0 or (args.max_videos_per_user is not None and args.max_videos_per_user <= 0):
                raise ConfigError("Hotscope page and video limits must be positive")
            # Validate the entire selection before connecting to GCS or the provider.
            for value in args.user + read_list(args.users_file):
                user_name(value)
            for value in args.video_id + read_list(args.videos_file):
                video_id(value)
        elif args.video_id or args.videos_file or args.max_videos_per_user:
            raise ConfigError("video selection requires Hotscope users")
        return {"discover": print_discovery, "run": run}[args.command](config, args.input_stage, args)
    except (ConfigError, DiscoveryFailure, ValueError) as exc:
        _log("error", str(exc))
        return 2
    except KeyboardInterrupt:
        _log("interrupted", "stopped by operator")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
