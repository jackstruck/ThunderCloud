#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from bulk_download.config import load_config
from bulk_download.fetch import Fetcher
from bulk_download.pipeline import load_inputs
from bulk_download.resolvers import anchor_links


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Resolve JustPaste pages to Luluvid URLs")
    parser.add_argument("--config", default="./config.toml")
    parser.add_argument("--output", default="./input/luluvid_urls.txt")
    parser.add_argument("--checkpoint", default="./data/justpaste_resolution.jsonl")
    parser.add_argument("--delay", type=float, default=0.75)
    return parser.parse_args()


def read_lines(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}


def completed_pages(path: Path) -> set[str]:
    if not path.exists():
        return set()
    result: set[str] = set()
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid checkpoint JSON on line {number}") from exc
            if record.get("status") == "complete":
                result.add(record["justpaste_url"])
    return result


def durable_append(handle, value: str) -> None:
    handle.write(value)
    handle.flush()
    os.fsync(handle.fileno())


def main() -> int:
    args = arguments()
    config = load_config(args.config)
    output = Path(args.output).resolve()
    checkpoint = Path(args.checkpoint).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    output.touch(exist_ok=True)
    checkpoint.touch(exist_ok=True)

    sources = load_inputs(config.justpaste_file, "justpaste")
    known = read_lines(output)
    completed = completed_pages(checkpoint)
    pending = [url for url in sources if url not in completed]
    failures = 0
    http_config = replace(config.http, attempts=5, backoff_seconds=10)

    print(
        f"sources={len(sources)} completed={len(completed)} pending={len(pending)} "
        f"existing_links={len(known)}",
        flush=True,
    )
    with output.open("a", encoding="utf-8") as output_handle, checkpoint.open(
        "a", encoding="utf-8"
    ) as checkpoint_handle, Fetcher(http_config) as fetcher:
        for index, source in enumerate(pending, 1):
            try:
                html, final = fetcher.html(source)
                found = anchor_links(html, final, "luluvid")
                if not found:
                    print(
                        f"{index}/{len(pending)} returned no Luluvid links; stopping before checkpoint",
                        file=sys.stderr,
                        flush=True,
                    )
                    print(
                        "site may be throttling; saved progress is safe—run the same command later to resume",
                        file=sys.stderr,
                        flush=True,
                    )
                    return 75
                added = 0
                for link in found:
                    if link not in known:
                        durable_append(output_handle, link + "\n")
                        known.add(link)
                        added += 1
                record = {
                    "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "status": "complete",
                    "justpaste_url": source,
                    "found": len(found),
                    "added": added,
                }
                durable_append(
                    checkpoint_handle,
                    json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n",
                )
                print(
                    f"{index}/{len(pending)} found={len(found)} added={added} total={len(known)}",
                    flush=True,
                )
            except KeyboardInterrupt:
                print("interrupted; completed progress is saved", file=sys.stderr, flush=True)
                return 130
            except Exception as exc:
                failures += 1
                print(
                    f"{index}/{len(pending)} failed={type(exc).__name__}: {exc}",
                    file=sys.stderr,
                    flush=True,
                )
                if "HTTP 429" in str(exc):
                    print(
                        "rate limit reached; saved progress is safe—run the same command later to resume",
                        file=sys.stderr,
                        flush=True,
                    )
                    return 75
            time.sleep(max(0, args.delay))
    print(f"complete links={len(known)} failures={failures}", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
