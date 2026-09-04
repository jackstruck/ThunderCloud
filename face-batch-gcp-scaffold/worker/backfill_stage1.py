from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from .config import Settings
from .db import Database


def load_inventory(path: Path, expected_sha256: str | None = None) -> dict[str, Any]:
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if expected_sha256 and digest != expected_sha256.lower():
        raise ValueError("inventory SHA-256 does not match the frozen inventory")
    inventory = json.loads(data)
    if inventory.get("schema_version") != 1 or not isinstance(
        inventory.get("items"), list
    ):
        raise ValueError("unsupported backfill inventory")
    return inventory


class Stage1Repository:
    def __init__(self, database):
        self.database = database

    def reconcile(self, item: dict[str, Any], expected_versions: dict[str, str]) -> str:
        if item["classification"] not in {"metadata_only", "complete", "gallery_only"}:
            return "ignored"
        gcs = item["gcs"]
        required = {
            "generation": gcs["generation"],
            "bytes": gcs["bytes"],
            "content_type": gcs["content_type"],
            "page_url": item["page_url"],
        }
        connection = self.database.connect()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """SELECT source_id, source_sha256, metadata
                   FROM source_asset
                   WHERE external_source_ref=%s FOR UPDATE""",
                (item["canonical_object_uri"],),
            )
            rows = cursor.fetchall()
            matching = [row for row in rows if str(row[1]) == item["sha256"]]
            if len(matching) != 1 or len(rows) != 1:
                connection.rollback()
                return "blocked"
            source_id, _, metadata = matching[0]
            metadata = dict(metadata or {})
            for key, value in required.items():
                if value is None:
                    connection.rollback()
                    return "blocked"
                current = metadata.get(key)
                if current not in (None, "", value):
                    connection.rollback()
                    return "blocked"
            cursor.execute(
                """SELECT job_id FROM processing_job
                   WHERE source_id=%s AND status='succeeded'
                     AND worker_version=%s AND detector_version=%s
                     AND embedding_model_version=%s AND threshold_version=%s""",
                (
                    source_id,
                    expected_versions["worker_version"],
                    expected_versions["detector_version"],
                    expected_versions["embedding_model_version"],
                    expected_versions["threshold_version"],
                ),
            )
            job_ids = {str(row[0]) for row in cursor.fetchall()}
            if not job_ids or not set(item["compatible_job_ids"]).issubset(job_ids):
                connection.rollback()
                return "blocked"
            cursor.execute(
                """SELECT count(*) FROM face_track
                   WHERE source_id=%s AND processing_job_id=ANY(%s::uuid[])""",
                (source_id, sorted(job_ids)),
            )
            if int(cursor.fetchone()[0]) != len(item["face_tracks"]):
                connection.rollback()
                return "blocked"
            changed = any(metadata.get(key) != value for key, value in required.items())
            if changed:
                metadata.update(required)
                cursor.execute(
                    "UPDATE source_asset SET metadata=%s::jsonb WHERE source_id=%s",
                    (json.dumps(metadata, separators=(",", ":")), source_id),
                )
            connection.commit()
            return "updated" if changed else "unchanged"
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def refresh_attribution(self) -> int:
        connection = self.database.connect()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """WITH refreshed AS (
                     SELECT candidate.run_id, candidate.group_id, candidate.subject_id,
                            COALESCE((
                              SELECT jsonb_agg(x.page_url ORDER BY x.page_url)
                              FROM (
                                SELECT DISTINCT asset.metadata->>'page_url' AS page_url
                                FROM source_asset asset
                                WHERE asset.metadata->>'page_url' IS NOT NULL
                                  AND asset.source_id IN (
                                    SELECT source_id FROM face_track
                                    WHERE subject_id=candidate.subject_id
                                    UNION
                                    SELECT source_id FROM submission_enrollment
                                    WHERE subject_id=candidate.subject_id
                                  )
                              ) x
                            ), '[]'::jsonb) AS page_urls
                     FROM run_candidate candidate
                   )
                   UPDATE run_candidate candidate SET page_urls=refreshed.page_urls
                   FROM refreshed
                   WHERE candidate.run_id=refreshed.run_id
                     AND candidate.group_id=refreshed.group_id
                     AND candidate.subject_id=refreshed.subject_id
                     AND candidate.page_urls IS DISTINCT FROM refreshed.page_urls"""
            )
            changed = cursor.rowcount
            connection.commit()
            return changed
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()


def reconcile(
    inventory: dict[str, Any], repository: Stage1Repository
) -> dict[str, int]:
    if any(item["classification"] == "blocked" for item in inventory["items"]):
        raise RuntimeError("frozen inventory contains blocked items")
    counts: Counter[str] = Counter()
    for item in inventory["items"]:
        if item["classification"] not in {
            "metadata_only",
            "complete",
            "gallery_only",
        }:
            counts["ignored"] += 1
            continue
        counts[repository.reconcile(item, inventory["expected_versions"])] += 1
    return dict(sorted(counts.items()))


def export_process_set(
    inventory: dict[str, Any], manifest: Path, selection: Path
) -> int:
    items = [item for item in inventory["items"] if item["classification"] == "process"]
    with manifest.open("w", encoding="utf-8") as output:
        for item in items:
            gcs = item["gcs"]
            output.write(
                json.dumps(
                    {
                        "status": "complete",
                        "uid": item["uid"],
                        "object": item["canonical_object_uri"],
                        "sha256": item["sha256"],
                        "generation": gcs["generation"],
                        "bytes": gcs["bytes"],
                        "content_type": gcs["content_type"],
                        "luluvid_url": item["page_url"],
                    },
                    separators=(",", ":"),
                )
                + "\n"
            )
    selection.write_text(
        "".join(f"{item['uid']}\n" for item in items), encoding="utf-8"
    )
    return len(items)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Stage 1 historical backfill tools")
    result.add_argument("--inventory", type=Path, required=True)
    result.add_argument("--inventory-sha256")
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("reconcile-metadata")
    export = commands.add_parser("export-process-set")
    export.add_argument("--manifest-output", type=Path, required=True)
    export.add_argument("--selection-output", type=Path, required=True)
    commands.add_parser("refresh-attribution")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    inventory = load_inventory(args.inventory, args.inventory_sha256)
    if args.command == "export-process-set":
        count = export_process_set(
            inventory, args.manifest_output, args.selection_output
        )
        print(json.dumps({"process_items": count}, separators=(",", ":")))
        return 0
    settings = Settings.from_env()
    settings.validate()
    database = Database(
        settings.cloud_sql_instance,
        settings.db_user,
        settings.db_name,
        settings.cloud_sql_ip_type,
    )
    try:
        repository = Stage1Repository(database)
        result = (
            reconcile(inventory, repository)
            if args.command == "reconcile-metadata"
            else {"candidate_rows_updated": repository.refresh_attribution()}
        )
    finally:
        database.close()
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 2 if result.get("blocked") else 0


if __name__ == "__main__":
    raise SystemExit(main())
