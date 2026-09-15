from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .storage import GcsUri, validate_sha256


@dataclass(frozen=True)
class ManifestItem:
    uid: str
    object_uri: str
    sha256: str | None
    bytes: int | None
    content_type: str | None
    timestamp: str | None
    generation: int | None
    source: dict[str, str]


def read_manifest(path: Path, bucket: str, source_prefix: str) -> list[ManifestItem]:
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"invalid manifest JSON on line {line_number}"
                ) from exc
            if not isinstance(record, dict):
                raise TypeError(f"manifest line {line_number} is not an object")
            records.append(record)

    completed_by_object = {
        item["object"]: item
        for item in records
        if item.get("status") == "complete" and isinstance(item.get("object"), str)
    }
    result: dict[str, ManifestItem] = {}
    for record in records:
        status = record.get("status")
        if status == "complete":
            resolved = record
        elif status == "duplicate" and isinstance(record.get("duplicate_of"), str):
            canonical = completed_by_object.get(record["duplicate_of"])
            if canonical is None:
                continue
            resolved = {**canonical, **record, "object": canonical["object"]}
        else:
            continue
        object_uri = resolved.get("object")
        uid = resolved.get("uid")
        if not isinstance(object_uri, str) or not isinstance(uid, str):
            continue
        uri = GcsUri.parse(object_uri)
        if uri.bucket != bucket or not uri.object_name.startswith(source_prefix):
            raise ValueError(
                f"manifest object is outside configured source prefix: {object_uri}"
            )
        sources = {
            key: _redact_url(value)
            for key, value in resolved.items()
            if key.endswith("_url") and isinstance(value, str)
        }
        result[uid] = ManifestItem(
            uid=uid,
            object_uri=object_uri,
            sha256=validate_sha256(resolved.get("sha256")),
            bytes=int(resolved["bytes"]) if resolved.get("bytes") is not None else None,
            content_type=resolved.get("content_type")
            if isinstance(resolved.get("content_type"), str)
            else None,
            timestamp=resolved.get("timestamp")
            if isinstance(resolved.get("timestamp"), str)
            else None,
            generation=int(resolved["generation"])
            if resolved.get("generation") is not None
            else None,
            source=sources,
        )
    return list(result.values())


def _redact_url(value: str) -> str:
    parsed = urlsplit(value)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def select_items(
    items: Iterable[ManifestItem], selectors: Iterable[str]
) -> list[ManifestItem]:
    wanted = {x.strip() for x in selectors if x.strip()}
    if not wanted:
        raise ValueError("selection is empty")
    matched: list[ManifestItem] = []
    seen: set[str] = set()
    consumed: set[str] = set()
    for item in items:
        aliases = {item.uid, item.object_uri}
        selected = aliases & wanted
        if not selected:
            continue
        consumed.update(selected)
        identity = item.sha256 or item.object_uri
        if identity not in seen:
            seen.add(identity)
            matched.append(item)
    missing = wanted - consumed
    if missing:
        raise ValueError(
            "selectors not found in usable manifest records: "
            + ", ".join(sorted(missing))
        )
    return matched
