import json

from worker.backfill_inventory import (
    ObjectRecord,
    build_inventory,
    unresolved_justpaste,
)

DIGEST = "a" * 64
VERSIONS = {
    "worker_version": "worker-1",
    "detector_version": "detector-1",
    "embedding_model_version": "embedding-1",
    "threshold_version": "threshold-1",
}


def manifest(line=1, *, status="complete", uid="one", page="https://luluvid.com/one"):
    result = {
        "_manifest_line": line,
        "status": status,
        "uid": uid,
        "object": "gs://bucket/videos/one.mp4",
        "sha256": DIGEST,
        "generation": 7,
        "bytes": 123,
        "content_type": "video/mp4",
        "luluvid_url": page,
    }
    return result


def gcs(*, digest=DIGEST, encrypted=True):
    uri = "gs://bucket/videos/one.mp4"
    return {
        uri: ObjectRecord(uri, 7, 123, "video/mp4", encrypted, digest),
    }


def job(status="succeeded", **overrides):
    result = {
        "job_id": "job-1",
        "idempotency_key": "key",
        "status": status,
        "error_code": None,
        "completed_at": None,
        **VERSIONS,
    }
    result.update(overrides)
    return result


def database(*, source=True, jobs=None, tracks=None, subjects=None):
    uri = "gs://bucket/videos/one.mp4"
    rows = []
    if source:
        for current_job in jobs or []:
            rows.append(
                {
                    "source_id": "source-1",
                    "source_sha256": DIGEST,
                    "metadata": {
                        "generation": 7,
                        "bytes": 123,
                        "content_type": "video/mp4",
                        "page_url": "https://luluvid.com/one",
                    },
                    "source_created_at": None,
                    "job": current_job,
                }
            )
        if not jobs:
            rows.append(
                {
                    "source_id": "source-1",
                    "source_sha256": DIGEST,
                    "metadata": {
                        "generation": 7,
                        "bytes": 123,
                        "content_type": "video/mp4",
                        "page_url": "https://luluvid.com/one",
                    },
                    "source_created_at": None,
                    "job": None,
                }
            )
    return {
        "sources": {uri: rows} if rows else {},
        "tracks": {"source-1": tracks or []},
        "subjects": subjects or {},
        "active_or_dead_letter_work": [],
    }


def classify(records, objects, snapshot):
    items, errors = build_inventory(records, objects, snapshot, VERSIONS, 5)
    assert not errors
    return items


def test_classifies_complete_and_process():
    complete = classify([manifest()], gcs(), database(jobs=[job()]))
    assert complete[0]["classification"] == "complete"

    process = classify([manifest()], gcs(), database(jobs=[job(status="failed")]))
    assert process[0]["classification"] == "process"


def test_classifies_missing_metadata_and_conflict_separately():
    snapshot = database(jobs=[job()])
    snapshot["sources"]["gs://bucket/videos/one.mp4"][0]["metadata"].pop("page_url")
    assert (
        classify([manifest()], gcs(), snapshot)[0]["classification"] == "metadata_only"
    )

    snapshot = database(jobs=[job()])
    snapshot["sources"]["gs://bucket/videos/one.mp4"][0]["metadata"]["generation"] = 8
    item = classify([manifest()], gcs(), snapshot)[0]
    assert item["classification"] == "blocked"
    assert "conflicts with manifest" in item["blocked_reasons"][0]


def test_classifies_gallery_only():
    track = {
        "track_id": "track-1",
        "processing_job_id": "job-1",
        "subject_id": "subject-1",
        "start_ms": 0,
        "end_ms": 10,
        "model_version": "embedding-1",
        "observation_count": 2,
        "embedded_count": 2,
        "max_quality": 1.0,
        "mean_quality": 1.0,
        "decision": "unknown",
    }
    snapshot = database(
        jobs=[job()],
        tracks=[track],
        subjects={
            "subject-1": {
                "subject_id": "subject-1",
                "model_version": "embedding-1",
                "sample_count": 1,
                "active_gallery": 4,
            },
        },
    )
    assert (
        classify([manifest()], gcs(), snapshot)[0]["classification"] == "gallery_only"
    )


def test_duplicate_requires_processed_canonical_source():
    base = manifest()
    duplicate = manifest(
        2, status="duplicate", uid="two", page="https://luluvid.com/two"
    )
    duplicate.pop("object")
    duplicate["duplicate_of"] = base["object"]
    items = classify([base, duplicate], gcs(), database(jobs=[job()]))
    assert [item["classification"] for item in items] == ["complete", "duplicate"]

    items = classify([base, duplicate], gcs(), database(jobs=[job(status="failed")]))
    assert [item["classification"] for item in items] == ["process", "process"]


def test_checksum_mismatch_and_ambiguous_provenance_are_blocked():
    item = classify([manifest()], gcs(digest="b" * 64), database(jobs=[job()]))[0]
    assert item["classification"] == "blocked"
    assert any("sha256" in reason for reason in item["blocked_reasons"])

    second = manifest(2, uid="two", page="https://luluvid.com/two")
    second["object"] = "gs://bucket/videos/two.mp4"
    objects = {
        **gcs(),
        second["object"]: ObjectRecord(
            second["object"], 7, 123, "video/mp4", True, DIGEST
        ),
    }
    items = classify([manifest(), second], objects, database(jobs=[job()]))
    assert all(item["classification"] == "blocked" for item in items)


def test_unresolved_justpaste_preserves_order_and_deduplicates(tmp_path):
    input_path = tmp_path / "urls.txt"
    checkpoint = tmp_path / "checkpoint.jsonl"
    input_path.write_text(
        "https://justpaste.it/new\nhttps://JUSTPASTE.IT/done\nhttps://justpaste.it/new\n"
    )
    checkpoint.write_text(
        json.dumps({"status": "complete", "justpaste_url": "https://justpaste.it/done"})
        + "\n"
    )
    assert unresolved_justpaste(input_path, checkpoint) == ["https://justpaste.it/new"]
