from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from google.api_core.exceptions import GoogleAPICallError

from .config import Settings
from .db import Database
from .storage import (
    has_customer_encryption,
    load_configured_csek,
    validate_sha256,
)

STATES = (
    "complete",
    "metadata_only",
    "gallery_only",
    "process",
    "blocked",
    "duplicate",
)


def _canonical_url(value: str) -> str:
    parts = urlsplit(value.strip())
    host = (parts.hostname or "").lower().rstrip(".")
    if parts.scheme.lower() != "https" or not host or parts.username or parts.password:
        raise ValueError(f"invalid HTTPS URL: {value}")
    if parts.port not in (None, 443):
        raise ValueError(f"non-default URL port: {value}")
    return urlunsplit(("https", host, parts.path or "/", parts.query, ""))


def _json_lines(path: Path) -> list[dict[str, Any]]:
    result = []
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON on {path}:{number}") from exc
            if not isinstance(value, dict):
                raise TypeError(f"expected object on {path}:{number}")
            value["_manifest_line"] = number
            result.append(value)
    return result


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def unresolved_justpaste(input_path: Path, checkpoint_path: Path) -> list[str]:
    completed: set[str] = set()
    if checkpoint_path.exists():
        for record in _json_lines(checkpoint_path):
            if record.get("status") == "complete" and isinstance(
                record.get("justpaste_url"), str
            ):
                completed.add(_canonical_url(record["justpaste_url"]))
    pending, seen = [], set()
    for raw in input_path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        url = _canonical_url(raw)
        if url not in seen and url not in completed:
            pending.append(url)
            seen.add(url)
    return pending


@dataclass(frozen=True)
class ObjectRecord:
    uri: str
    generation: int
    bytes: int
    content_type: str | None
    customer_encrypted: bool
    sha256: str | None
    error: str | None = None


class GcsInventory:
    def __init__(self, project: str, bucket: str, prefix: str, csek: bytes):
        from google.cloud import storage

        self.bucket_name = bucket
        self.prefix = prefix
        self.csek = csek
        self.client = storage.Client(project=project)
        self.bucket = self.client.bucket(bucket)

    def list_and_hash(self) -> dict[str, ObjectRecord]:
        result: dict[str, ObjectRecord] = {}
        for listed in self.client.list_blobs(self.bucket_name, prefix=self.prefix):
            uri = f"gs://{self.bucket_name}/{listed.name}"
            blob = self.bucket.blob(
                listed.name, generation=int(listed.generation), encryption_key=self.csek
            )
            try:
                blob.reload()
                digest = hashlib.sha256()
                with blob.open("rb") as handle:
                    while block := handle.read(8 * 1024 * 1024):
                        digest.update(block)
                result[uri] = ObjectRecord(
                    uri,
                    int(blob.generation),
                    int(blob.size),
                    blob.content_type,
                    has_customer_encryption(blob),
                    digest.hexdigest(),
                )
            except (GoogleAPICallError, OSError, ValueError) as exc:
                # Preserve each inaccessible object in the audit.
                result[uri] = ObjectRecord(
                    uri,
                    int(listed.generation),
                    int(listed.size or 0),
                    listed.content_type,
                    False,
                    None,
                    f"{type(exc).__name__}: {exc}",
                )
        return result


class InventoryDatabase:
    def __init__(self, database: Database):
        self.database = database

    def snapshot(self, source_uris: list[str]) -> dict[str, Any]:
        connection = self.database.connect()
        cursor = connection.cursor()
        try:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            cursor.execute(
                """SELECT sa.source_id, sa.external_source_ref, sa.source_sha256,
                          sa.metadata, sa.created_at,
                          pj.job_id, pj.idempotency_key, pj.worker_version,
                          pj.detector_version, pj.embedding_model_version,
                          pj.threshold_version, pj.status, pj.error_code, pj.completed_at
                   FROM source_asset sa
                   LEFT JOIN processing_job pj ON pj.source_id=sa.source_id
                   WHERE sa.external_source_ref = ANY(%s)
                   ORDER BY sa.external_source_ref, sa.created_at, pj.created_at""",
                (source_uris,),
            )
            sources: dict[str, list[dict[str, Any]]] = defaultdict(list)
            source_ids: list[str] = []
            for row in cursor.fetchall():
                source_id = str(row[0])
                source_ids.append(source_id)
                sources[str(row[1])].append(
                    {
                        "source_id": source_id,
                        "source_sha256": str(row[2]),
                        "metadata": row[3] or {},
                        "source_created_at": row[4],
                        "job": None
                        if row[5] is None
                        else {
                            "job_id": str(row[5]),
                            "idempotency_key": row[6],
                            "worker_version": row[7],
                            "detector_version": row[8],
                            "embedding_model_version": row[9],
                            "threshold_version": row[10],
                            "status": row[11],
                            "error_code": row[12],
                            "completed_at": row[13],
                        },
                    }
                )
            cursor.execute(
                """SELECT ft.track_id, ft.source_id, ft.processing_job_id, ft.subject_id,
                          ft.start_ms, ft.end_ms, ft.model_version, ft.observation_count,
                          ft.embedded_count, ft.max_quality, ft.mean_quality, ft.decision
                   FROM face_track ft WHERE ft.source_id = ANY(%s::uuid[])
                   ORDER BY ft.source_id, ft.processing_job_id, ft.local_track_id""",
                (sorted(set(source_ids)),),
            )
            tracks: dict[str, list[dict[str, Any]]] = defaultdict(list)
            subject_ids: set[str] = set()
            for row in cursor.fetchall():
                subject_id = None if row[3] is None else str(row[3])
                if subject_id:
                    subject_ids.add(subject_id)
                tracks[str(row[1])].append(
                    {
                        "track_id": str(row[0]),
                        "processing_job_id": str(row[2]),
                        "subject_id": subject_id,
                        "start_ms": int(row[4]),
                        "end_ms": int(row[5]),
                        "model_version": row[6],
                        "observation_count": row[7],
                        "embedded_count": row[8],
                        "max_quality": row[9],
                        "mean_quality": row[10],
                        "decision": row[11],
                    }
                )
            cursor.execute(
                """SELECT subject_id, model_version, sample_count, metadata,
                          created_at, updated_at
                   FROM subject WHERE subject_id = ANY(%s::uuid[])
                   ORDER BY subject_id""",
                (sorted(subject_ids),),
            )
            subjects = {
                str(r[0]): {
                    "subject_id": str(r[0]),
                    "model_version": r[1],
                    "sample_count": r[2],
                    "metadata": r[3] or {},
                    "created_at": r[4],
                    "updated_at": r[5],
                    "active_gallery": 0,
                    "representative_faces": [],
                }
                for r in cursor.fetchall()
            }
            cursor.execute(
                """SELECT representative_id, subject_id, source_id, source_track_id,
                          object_name, object_generation, content_type,
                          source_timestamp_ms, quality_score, active, created_at, retired_at
                   FROM subject_representative_face
                   WHERE subject_id = ANY(%s::uuid[])
                   ORDER BY subject_id, created_at, representative_id""",
                (sorted(subject_ids),),
            )
            for row in cursor.fetchall():
                subject = subjects[str(row[1])]
                representative = {
                    "representative_id": str(row[0]),
                    "source_id": None if row[2] is None else str(row[2]),
                    "source_track_id": None if row[3] is None else str(row[3]),
                    "object_name": row[4],
                    "object_generation": row[5],
                    "content_type": row[6],
                    "source_timestamp_ms": row[7],
                    "quality_score": row[8],
                    "active": row[9],
                    "created_at": row[10],
                    "retired_at": row[11],
                }
                subject["representative_faces"].append(representative)
                subject["active_gallery"] += int(bool(row[9]))
            cursor.execute(
                """SELECT wi.work_item_id, wi.rollout_id, r.name, r.status, wi.manifest_uid,
                          wi.source_uri, wi.source_sha256, wi.source_generation,
                          wi.state, wi.attempt_count, wi.max_attempts, wi.last_error_code
                   FROM processing_work_item wi JOIN processing_rollout r USING(rollout_id)
                   WHERE wi.source_uri = ANY(%s) AND wi.state IN ('pending','leased','retry','dead_letter')
                   ORDER BY wi.created_at""",
                (source_uris,),
            )
            work = [
                {
                    "work_item_id": str(r[0]),
                    "rollout_id": str(r[1]),
                    "rollout_name": r[2],
                    "rollout_status": r[3],
                    "manifest_uid": r[4],
                    "source_uri": r[5],
                    "source_sha256": str(r[6]),
                    "source_generation": r[7],
                    "state": r[8],
                    "attempt_count": r[9],
                    "max_attempts": r[10],
                    "last_error_code": r[11],
                }
                for r in cursor.fetchall()
            ]
            connection.rollback()
            return {
                "sources": dict(sources),
                "tracks": dict(tracks),
                "subjects": subjects,
                "active_or_dead_letter_work": work,
            }
        finally:
            cursor.close()
            connection.close()


def _compatible(job: dict[str, Any], expected: dict[str, str]) -> bool:
    return job["status"] == "succeeded" and all(
        job[key] == value for key, value in expected.items()
    )


def build_inventory(
    manifest_records: list[dict[str, Any]],
    objects: dict[str, ObjectRecord],
    database: dict[str, Any],
    expected_versions: dict[str, str],
    target_gallery: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    completed_by_object = {
        r.get("object"): r
        for r in manifest_records
        if r.get("status") == "complete" and isinstance(r.get("object"), str)
    }
    canonical_hash_provenance: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for record in manifest_records:
        if record.get("status") == "complete" and record.get("sha256"):
            canonical_hash_provenance[str(record["sha256"])].add(
                (str(record.get("object")), str(record.get("luluvid_url")))
            )
    ambiguous = {
        digest
        for digest, provenance in canonical_hash_provenance.items()
        if len(provenance) > 1
    }
    usable, errors = [], []
    for raw in manifest_records:
        if raw.get("status") == "complete":
            resolved, duplicate = raw, False
        elif raw.get("status") == "duplicate" and isinstance(
            raw.get("duplicate_of"), str
        ):
            base = completed_by_object.get(raw["duplicate_of"])
            if base is None:
                errors.append(
                    f"line {raw['_manifest_line']}: duplicate target is absent"
                )
                continue
            resolved, duplicate = (
                {**base, **raw, "object": base["object"], "sha256": base["sha256"]},
                True,
            )
        else:
            continue
        uri, reasons = resolved.get("object"), []
        try:
            digest = validate_sha256(resolved.get("sha256"))
        except ValueError as exc:
            digest = None
            reasons.append(str(exc))
        obj = objects.get(uri)
        if obj is None:
            reasons.append("manifest object is missing from GCS inventory")
        else:
            for field, actual in (
                ("generation", obj.generation),
                ("bytes", obj.bytes),
                ("content_type", obj.content_type),
                ("sha256", obj.sha256),
            ):
                expected = digest if field == "sha256" else resolved.get(field)
                if expected is None or str(expected) != str(actual):
                    reasons.append(
                        f"manifest {field}={expected!r} differs from GCS {actual!r}"
                    )
            if not obj.customer_encrypted:
                reasons.append(
                    "GCS object does not report customer-supplied encryption"
                )
            if obj.error:
                reasons.append(f"GCS inspection failed: {obj.error}")
        if digest in ambiguous:
            reasons.append("content hash maps to conflicting canonical provenance")
        db_sources = database["sources"].get(uri, [])
        matching_sources = [s for s in db_sources if s["source_sha256"] == digest]
        if len({s["source_sha256"] for s in db_sources}) > 1:
            reasons.append(
                "database has conflicting source ownership/checksums for object"
            )
        jobs = [s["job"] for s in matching_sources if s.get("job")]
        successful = [j for j in jobs if _compatible(j, expected_versions)]
        source_ids = {s["source_id"] for s in matching_sources}
        tracks = [
            t
            for sid in source_ids
            for t in database["tracks"].get(sid, [])
            if any(t["processing_job_id"] == j["job_id"] for j in successful)
        ]
        subject_ids = {t["subject_id"] for t in tracks if t["subject_id"]}
        gallery_missing = [
            sid
            for sid in subject_ids
            if int(database["subjects"].get(sid, {}).get("active_gallery") or 0)
            < target_gallery
        ]
        metadata_missing = []
        metadata_conflicts = []
        page_url = resolved.get("luluvid_url")
        for source in matching_sources if not duplicate else []:
            metadata = source["metadata"]
            required = {
                "generation": resolved.get("generation"),
                "bytes": resolved.get("bytes"),
                "content_type": resolved.get("content_type"),
                "page_url": page_url,
            }
            for key, value in required.items():
                if value is None or metadata.get(key) == value:
                    continue
                if metadata.get(key) in (None, ""):
                    metadata_missing.append(key)
                else:
                    metadata_conflicts.append(
                        f"source metadata {key}={metadata[key]!r} conflicts with manifest {value!r}"
                    )
        reasons.extend(metadata_conflicts)
        if reasons:
            state = "blocked"
        elif duplicate and successful:
            state = "duplicate"
        elif not successful:
            state = "process"
        elif metadata_missing or not matching_sources:
            state = "metadata_only"
        elif gallery_missing:
            state = "gallery_only"
        else:
            state = "complete"
        usable.append(
            {
                "manifest_line": raw["_manifest_line"],
                "uid": raw.get("uid"),
                "canonical_object_uri": uri,
                "sha256": digest,
                "page_url": page_url,
                "manifest_status": raw.get("status"),
                "classification": state,
                "blocked_reasons": sorted(set(reasons)),
                "metadata_fields_needing_repair": sorted(set(metadata_missing)),
                "gcs": None if obj is None else asdict(obj),
                "source_assets": db_sources,
                "compatible_job_ids": [j["job_id"] for j in successful],
                "face_tracks": tracks,
                "subjects": [
                    database["subjects"].get(sid) for sid in sorted(subject_ids)
                ],
                "active_or_dead_letter_work": [
                    w
                    for w in database["active_or_dead_letter_work"]
                    if w["source_uri"] == uri
                ],
            }
        )
    return usable, errors


def _json_default(value: Any) -> str:
    if isinstance(value, (datetime,)):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(
                value,
                handle,
                sort_keys=True,
                separators=(",", ":"),
                default=_json_default,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Create the read-only historical backfill inventory"
    )
    result.add_argument(
        "--manifest", type=Path, default=Path("../bulk-download/data/manifest.jsonl")
    )
    result.add_argument(
        "--justpaste-input",
        type=Path,
        default=Path("../bulk-download/input/justpaste_urls.txt"),
    )
    result.add_argument(
        "--justpaste-checkpoint",
        type=Path,
        default=Path("../bulk-download/data/justpaste_resolution.jsonl"),
    )
    result.add_argument("--output-dir", type=Path, default=Path("data"))
    result.add_argument("--target-gallery", type=int, default=5)
    result.add_argument("--worker-version", required=True)
    result.add_argument("--detector-version", required=True)
    result.add_argument("--embedding-model-version", required=True)
    result.add_argument("--threshold-version", required=True)
    return result


def run(args: argparse.Namespace) -> tuple[int, list[Path]]:
    if args.target_gallery <= 0:
        raise ValueError("target gallery must be positive")
    cutoff = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    stamp = cutoff.replace("-", "").replace(":", "").replace(".", "").replace("Z", "Z")
    before_hash = sha256_file(args.manifest)
    manifest_records = _json_lines(args.manifest)
    settings = Settings.from_env()
    settings.validate()
    csek = load_configured_csek(
        settings.project_id, settings.csek_file, settings.csek_secret
    )
    objects = GcsInventory(
        settings.project_id, settings.bucket, settings.source_prefix, csek
    ).list_and_hash()
    usable_uris = sorted(
        {
            str(r.get("object"))
            for r in manifest_records
            if r.get("status") in {"complete", "duplicate"} and r.get("object")
        }
    )
    database = Database(
        settings.cloud_sql_instance,
        settings.db_user,
        settings.db_name,
        settings.cloud_sql_ip_type,
    )
    try:
        snapshot = InventoryDatabase(database).snapshot(usable_uris)
    finally:
        database.close()
    after_hash = sha256_file(args.manifest)
    if before_hash != after_hash:
        raise RuntimeError(
            "manifest changed while inventory was being generated; retry from a stable cutoff"
        )
    expected = {
        "worker_version": args.worker_version,
        "detector_version": args.detector_version,
        "embedding_model_version": args.embedding_model_version,
        "threshold_version": args.threshold_version,
    }
    items, parse_errors = build_inventory(
        manifest_records, objects, snapshot, expected, args.target_gallery
    )
    pending = unresolved_justpaste(args.justpaste_input, args.justpaste_checkpoint)
    counts = Counter(item["classification"] for item in items)
    manifest_objects = {item["canonical_object_uri"] for item in items}
    gcs_orphans = sorted(set(objects) - manifest_objects)
    inventory = {
        "schema_version": 1,
        "cutoff_timestamp": cutoff,
        "manifest_path": str(args.manifest.resolve()),
        "manifest_sha256": before_hash,
        "expected_versions": expected,
        "target_active_gallery_representatives": args.target_gallery,
        "items": items,
        "gcs_objects_not_in_usable_manifest": [asdict(objects[u]) for u in gcs_orphans],
        "unresolved_justpaste": {"count": len(pending), "urls": pending},
        "manifest_parse_errors": parse_errors,
    }
    inventory_path = args.output_dir / f"bulk-backfill-inventory-{stamp}.json"
    _atomic_json(inventory_path, inventory)
    inventory_hash = sha256_file(inventory_path)
    blocked = counts["blocked"]
    summary = {
        "schema_version": 1,
        "cutoff_timestamp": cutoff,
        "inventory_path": str(inventory_path.resolve()),
        "inventory_sha256": inventory_hash,
        "manifest_sha256": before_hash,
        "usable_manifest_records": len(items),
        "classification_counts": {state: counts[state] for state in STATES},
        "gcs_object_count": len(objects),
        "gcs_objects_not_in_usable_manifest": len(gcs_orphans),
        "unresolved_justpaste_count": len(pending),
        "manifest_parse_error_count": len(parse_errors),
        "totals_reconcile": sum(counts.values()) == len(items),
        "acceptance_gate_passed": not blocked
        and not parse_errors
        and sum(counts.values()) == len(items),
        "blocked_review_required": blocked > 0 or bool(parse_errors),
    }
    summary_path = args.output_dir / f"bulk-backfill-summary-{stamp}.json"
    _atomic_json(summary_path, summary)
    print(
        json.dumps(
            {"inventory": str(inventory_path), "summary": str(summary_path), **summary},
            default=_json_default,
        )
    )
    return (2 if summary["blocked_review_required"] else 0), [
        inventory_path,
        summary_path,
    ]


def main(argv: list[str] | None = None) -> int:
    code, _ = run(parser().parse_args(argv))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
