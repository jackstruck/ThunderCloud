"""Dispose of exact recorded cloud artifact generations after retention expires."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, date, datetime, time
from pathlib import Path

from google.api_core.exceptions import NotFound

from maintenance.cli import atomic_json
from maintenance.rehearsal import file_checksum


def purge(resources, receipt_path, *, staging_path=None, client=None, now=None):
    from google.cloud import storage

    record = json.loads(Path(resources).read_text())
    if record.get("stage") != "cleaned" or not record.get("cleanup_export"):
        raise ValueError(
            "Verified job cleanup and artifact export are required before disposal"
        )
    export = record["cleanup_export"]
    retained = export["retained"]
    if retained.get("hold_reason") or record.get("hold_reason"):
        raise ValueError("Recovery artifacts remain on hold")
    deadline = datetime.combine(date.fromisoformat(retained["deadline"]), time.max, UTC)
    if (now or datetime.now(UTC)) <= deadline:
        raise ValueError("Recovery retention period has not expired")
    objects = list(export["objects"])
    if record.get("control_prefix"):
        control_export = record.get("cleanup_control_export")
        if (
            not control_export
            or control_export["generation"] != record["control_generation"]
            or control_export["uri"]
            != record["control_prefix"].rstrip("/") + "/resources.json"
        ):
            raise ValueError("Final cleanup control generation must be exported")
        objects.append(control_export)
    prefix = record["output_prefix"].rstrip("/") + "/"
    prefixes = [prefix]
    if staging_path is not None:
        staging_path = Path(staging_path)
        staged = json.loads(staging_path.read_text())
        if staged["objects"].get("request") != record["request"]:
            raise ValueError("Staging manifest belongs to another migration request")
        prefixes.append(staged["input_prefix"].rstrip("/") + "/")
        for name, item in staged["objects"].items():
            local = (
                (staging_path.parent / "request.json")
                if name == "request"
                else Path(staged["planned"][name]["path"])
            )
            objects.append({**item, "local": str(local)})
    for item in objects:
        if (
            not any(item["uri"].startswith(prefix) for prefix in prefixes)
            or type(item["generation"]) is not int
            or item["generation"] <= 0
        ):
            raise ValueError("Disposal object is outside the recorded output prefix")
        local = Path(item["local"])
        if (
            local.stat().st_size != item["bytes"]
            or file_checksum(local) != item["sha256"]
        ):
            raise ValueError(
                "Local export changed or is unavailable; cloud artifacts are retained"
            )
    client = client or storage.Client()
    receipt_path = Path(receipt_path)
    receipt_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    receipt = {
        "format": 1,
        "job": record["job"],
        "owner": retained["owner"],
        "removed": [],
        "remaining": [
            "Required image digests remain subject to deployment/rollback retention",
            *(
                []
                if staging_path is not None
                else [
                    "Recovery input objects remain retained; supply their staging manifest to dispose of them"
                ]
            ),
        ],
    }
    atomic_json(receipt_path, receipt)
    for item in objects:
        bucket, name = item["uri"][5:].split("/", 1)
        try:
            client.bucket(bucket).blob(name, generation=item["generation"]).delete(
                if_generation_match=item["generation"], timeout=30
            )
        except NotFound:
            pass
        receipt["removed"].append(
            {"uri": item["uri"], "generation": item["generation"]}
        )
        atomic_json(receipt_path, receipt)
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resources", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--staging", type=Path)
    args = parser.parse_args(argv)
    print(
        json.dumps(
            purge(args.resources, args.receipt, staging_path=args.staging), indent=2
        )
    )


if __name__ == "__main__":
    main()
