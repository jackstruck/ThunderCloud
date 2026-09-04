import json

import pytest

from worker.backfill_stage1 import export_process_set, load_inventory, reconcile


def inventory(classifications=("metadata_only", "process")):
    return {
        "schema_version": 1,
        "expected_versions": {"worker_version": "worker"},
        "items": [
            {
                "classification": classification,
                "uid": f"item-{index}",
                "canonical_object_uri": f"gs://bucket/videos/{index}.mp4",
                "sha256": str(index) * 64,
                "page_url": f"https://luluvid.com/{index}",
                "gcs": {
                    "generation": index,
                    "bytes": index * 10,
                    "content_type": "video/mp4",
                },
            }
            for index, classification in enumerate(classifications, 1)
        ],
    }


def test_load_inventory_checks_frozen_digest(tmp_path):
    path = tmp_path / "inventory.json"
    path.write_text(json.dumps(inventory()))
    with pytest.raises(ValueError, match="frozen inventory"):
        load_inventory(path, "0" * 64)


def test_reconcile_is_bounded_by_inventory_classification():
    class Repository:
        def reconcile(self, item, versions):
            assert versions == {"worker_version": "worker"}
            return "updated"

    assert reconcile(inventory(), Repository()) == {"ignored": 1, "updated": 1}


def test_export_process_set_is_accepted_by_ingest_manifest(tmp_path):
    manifest = tmp_path / "manifest.jsonl"
    selection = tmp_path / "selection.txt"
    assert export_process_set(inventory(), manifest, selection) == 1
    record = json.loads(manifest.read_text())
    assert record["uid"] == "item-2"
    assert selection.read_text() == "item-2\n"
