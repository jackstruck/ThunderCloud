from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def completed_urls(path: Path) -> set[str]:
    result: set[str] = set()
    if not path.exists():
        return result
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid manifest JSON on line {line_number}") from exc
            if item.get("status") in {"complete", "duplicate"} and isinstance(item.get("luluvid_url"), str):
                result.add(item["luluvid_url"])
    return result


def completed_hashes(path: Path) -> dict[str, dict[str, Any]]:
    """Return the first completed GCS object recorded for each SHA-256 digest."""
    result: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return result
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid manifest JSON on line {line_number}") from exc
            digest = item.get("sha256")
            if (
                item.get("status") == "complete"
                and isinstance(digest, str)
                and len(digest) == 64
                and isinstance(item.get("object"), str)
            ):
                result.setdefault(digest, item)
    return result


def append(path: Path, item: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(item, separators=(",", ":"), sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
