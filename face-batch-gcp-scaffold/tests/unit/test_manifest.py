import json

import pytest

from worker.manifest import read_manifest, select_items

BUCKET = "teak-banner-dome-bulk-videos"


def write_manifest(tmp_path, rows):
    path = tmp_path / "manifest.jsonl"
    path.write_text("".join(json.dumps(x) + "\n" for x in rows), encoding="utf-8")
    return path


def test_manifest_complete_duplicate_and_failed(tmp_path):
    digest = "a" * 64
    canonical = f"gs://{BUCKET}/videos/a.mp4"
    path = write_manifest(
        tmp_path,
        [
            {
                "status": "complete",
                "uid": "a",
                "object": canonical,
                "sha256": digest,
                "bytes": 10,
            },
            {
                "status": "duplicate",
                "uid": "b",
                "duplicate_of": canonical,
                "sha256": digest,
                "luluvid_url": "https://example.test/b?temporary=secret#fragment",
            },
            {"status": "failed", "uid": "c"},
        ],
    )
    items = read_manifest(path, BUCKET, "videos/")
    assert [x.uid for x in items] == ["a", "b"]
    selected = select_items(items, ["a", "b"])
    assert len(selected) == 1
    assert selected[0].object_uri == canonical
    duplicate = next(item for item in items if item.uid == "b")
    assert duplicate.source["luluvid_url"] == "https://example.test/b"


def test_manifest_rejects_object_outside_prefix(tmp_path):
    path = write_manifest(
        tmp_path,
        [
            {
                "status": "complete",
                "uid": "x",
                "object": f"gs://{BUCKET}/face-staging/x.mp4",
            }
        ],
    )
    with pytest.raises(ValueError, match="outside configured source"):
        read_manifest(path, BUCKET, "videos/")


def test_selection_reports_missing_selector(tmp_path):
    path = write_manifest(
        tmp_path,
        [{"status": "complete", "uid": "a", "object": f"gs://{BUCKET}/videos/a.mp4"}],
    )
    with pytest.raises(ValueError, match="not found"):
        select_items(read_manifest(path, BUCKET, "videos/"), ["missing"])


def test_latest_usable_record_wins_for_uid(tmp_path):
    path = write_manifest(
        tmp_path,
        [
            {
                "status": "complete",
                "uid": "a",
                "object": f"gs://{BUCKET}/videos/old.mp4",
            },
            {
                "status": "complete",
                "uid": "a",
                "object": f"gs://{BUCKET}/videos/new.mp4",
            },
        ],
    )
    items = read_manifest(path, BUCKET, "videos/")
    assert len(items) == 1
    assert items[0].object_uri.endswith("/new.mp4")
