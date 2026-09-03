#!/usr/bin/env bash
set -euo pipefail

python_bin="${PYTHON_BIN:-python}"
container_name="face-batch-db-test-$$"

cleanup() {
  docker stop "${container_name}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker run --rm -d --name "${container_name}" -P \
  -e POSTGRES_PASSWORD=test -e POSTGRES_DB=face_index \
  pgvector/pgvector:pg17 >/dev/null

for _ in 1 2 3 4 5 6 7 8 9 10; do
  if docker exec "${container_name}" pg_isready -U postgres -d face_index >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
docker exec "${container_name}" pg_isready -U postgres -d face_index >/dev/null
docker exec -i "${container_name}" psql -v ON_ERROR_STOP=1 -U postgres -d face_index \
  < scripts/db_schema.sql >/dev/null
docker exec -i "${container_name}" psql -v ON_ERROR_STOP=1 -U postgres -d face_index \
  < migrations/001_phase1_runs.sql >/dev/null

postgres_port="$(docker port "${container_name}" 5432/tcp | awk -F: 'NR==1 {print $NF}')"
FACE_TEST_POSTGRES_PORT="${postgres_port}" "${python_bin}" - <<'PY'
import os
import uuid

import pg8000

from worker.db import Database, Versions
from worker.manifest import ManifestItem
from worker.models import Candidate, TrackTemplate
from worker.queue import QueueDatabase
from worker.run_repository import RunConflictError, RunRepository
from worker.interactive_repository import DetectionWork, InteractiveRepository
from worker.gallery_backfill import BackfillRepository

port = int(os.environ["FACE_TEST_POSTGRES_PORT"])


def connect():
    return pg8000.connect(
        host="127.0.0.1",
        port=port,
        user="postgres",
        password="test",
        database="face_index",
    )


database = Database("unused", "unused", "unused")
database.connect = connect
embedding = [1.0] + [0.0] * 511
template = TrackTemplate(1, 0, 1000, 3, [Candidate(0.9, 0, embedding)], embedding)
arguments = {
    "job_id": "4a0e791d-fc5e-44ae-922f-9b71d91dca90",
    "idempotency_key": "integration-key",
    "external_source_ref": "gs://teak-banner-dome-bulk-videos/videos/test.mp4",
    "source_sha256": "a" * 64,
    "source_metadata": {"uid": "test"},
    "versions": Versions("worker", "detector", "embedding", "threshold"),
    "templates": [template],
    "top_k": 5,
    "threshold": 0.5,
    "matching_enabled": False,
}
assert database.commit_results(**arguments) is True
arguments["job_id"] = "9c78db6f-ceae-49f2-af48-4bde62ca6d68"
assert database.commit_results(**arguments) is False

connection = connect()
cursor = connection.cursor()
cursor.execute(
    "SELECT (SELECT count(*) FROM processing_job), "
    "(SELECT count(*) FROM face_track), (SELECT count(*) FROM subject)"
)
assert cursor.fetchone() == [1, 1, 1]
cursor.close()
connection.close()

# Probe search uses a read-only transaction and returns live candidate provenance.
before = connect(); before_cursor = before.cursor()
before_cursor.execute("SELECT subject_id, canonical_embedding::text FROM subject ORDER BY subject_id")
gallery_before = before_cursor.fetchall(); before_cursor.close(); before.close()
ranked = database.search_subjects([embedding], "embedding", 10)
assert len(ranked) == 1 and len(ranked[0]) == 1
assert ranked[0][0].similarity > .999
assert ranked[0][0].display_name is None
assert ranked[0][0].observations[0]["video_uri"] == "gs://teak-banner-dome-bulk-videos/videos/test.mp4"
after = connect(); after_cursor = after.cursor()
after_cursor.execute("SELECT subject_id, canonical_embedding::text FROM subject ORDER BY subject_id")
assert after_cursor.fetchall() == gallery_before
after_cursor.execute("SELECT to_regclass('probe'), to_regclass('probe_face'), to_regclass('probe_match')")
assert after_cursor.fetchone() == [None, None, None]
after_cursor.close(); after.close()

class MutatingProbeDatabase(Database):
    @staticmethod
    def rank_subjects(cursor, embedding, embedding_model_version, top_k):
        cursor.execute("DELETE FROM subject")
        return []

mutating = MutatingProbeDatabase("unused", "unused", "unused")
mutating.connect = connect
try:
    mutating.search_subjects([embedding], "embedding", 1)
except Exception as exc:
    assert "read-only" in str(exc).lower()
else:
    raise AssertionError("probe transaction unexpectedly permitted gallery mutation")
check = connect(); check_cursor = check.cursor()
check_cursor.execute("SELECT count(*) FROM subject")
assert check_cursor.fetchone() == [1]
check_cursor.close(); check.close()

queue_database = Database("unused", "unused", "unused")
queue_database.connect = connect
queue = QueueDatabase(queue_database)
items = [
    ManifestItem(
        f"uid-{index}",
        f"gs://teak-banner-dome-bulk-videos/videos/queue-{index}.mp4",
        str(index) * 64,
        100 + index,
        "video/mp4",
        None,
        index,
        {},
    )
    for index in (1, 2)
]
rollout_id, count = queue.create_rollout(
    name="integration",
    request_key="integration-request",
    image_digest="us-central1-docker.pkg.dev/p/r/i@sha256:" + "a" * 64,
    versions=Versions("worker", "detector", "embedding", "threshold"),
    creator_principal="test@example.com",
    configuration={"matching_enabled": True},
    items=items,
)
assert count == 2
assert queue.create_rollout(
    name="ignored-on-idempotent-retry",
    request_key="integration-request",
    image_digest="us-central1-docker.pkg.dev/p/r/i@sha256:" + "a" * 64,
    versions=Versions("worker", "detector", "embedding", "threshold"),
    creator_principal="test@example.com",
    configuration={"matching_enabled": True},
    items=items[:1],
) == (rollout_id, 2)
queue.start_rollout(rollout_id)
first = queue.claim(rollout_id)
second = queue.claim(rollout_id)
assert first is not None and second is not None
assert first.work_item_id != second.work_item_id
assert queue.heartbeat(first)
queue.record_staging(first, "gs://bucket/face-staging/a/video.mp4", 7)
queue.succeed(first)
queue.fail(second, "TEST_PERMANENT", retryable=False)
status = queue.refresh_rollout(rollout_id)
assert status == {
    "requested": 2,
    "succeeded": 1,
    "retryable": 0,
    "dead_letter": 1,
    "active": 0,
    "status": "failed",
}

lease_rollout_id, _ = queue.create_rollout(
    name="lease-recovery",
    request_key="lease-recovery-request",
    image_digest="us-central1-docker.pkg.dev/p/r/i@sha256:" + "b" * 64,
    versions=Versions("worker", "detector", "embedding", "threshold"),
    creator_principal="test@example.com",
    configuration={"matching_enabled": True},
    items=items[:1],
    max_attempts=1,
)
queue.start_rollout(lease_rollout_id)
abandoned = queue.claim(lease_rollout_id, lease_minutes=1)
assert abandoned is not None
connection = connect()
cursor = connection.cursor()
cursor.execute(
    "UPDATE processing_work_item SET lease_expires_at=now()-interval '1 minute' "
    "WHERE work_item_id=%s",
    (abandoned.work_item_id,),
)
connection.commit()
cursor.close()
connection.close()
assert queue.expire_exhausted_leases(lease_rollout_id) == 1
assert queue.refresh_rollout(lease_rollout_id)["status"] == "failed"

# Phase 1 run creation is payload-idempotent, quota-aware, and team-readable.
runs = RunRepository(database)
upload_payload = {
    "handling_policy": "search_then_discard",
    "source": {"kind": "upload", "content_type": "image/jpeg", "bytes": 123},
}
created = runs.create("member@example.com", "0123456789abcdef", upload_payload)
assert created.created and created.record["state"] == "awaiting_media"
replayed = runs.create("member@example.com", "0123456789abcdef", upload_payload)
assert not replayed.created and replayed.record["run_id"] == created.record["run_id"]
try:
    runs.create(
        "member@example.com",
        "0123456789abcdef",
        {**upload_payload, "source": {**upload_payload["source"], "bytes": 124}},
    )
except RunConflictError:
    pass
else:
    raise AssertionError("idempotency key accepted a different payload")
upload = runs.prepare_upload(created.record["run_id"])
assert upload == {
    "object_name": f"submissions-temporary/{created.record['run_id']}/source.jpg",
    "content_type": "image/jpeg",
    "expected_bytes": 123,
}
queued = runs.finalize_upload(
    created.record["run_id"],
    expected_bytes=123,
    expected_sha256="f" * 64,
    object_generation=7,
    object_bytes=123,
)
assert queued["state"] == "queued"
assert runs.get(created.record["run_id"])["run_id"] == created.record["run_id"]

# Detection, optimistic selection, model-compatible read-only ranking, and candidate
# snapshotting execute against the real Phase 1 constraints.
connection = connect(); cursor = connection.cursor()
cursor.execute(
    "UPDATE media_run SET state='detecting' WHERE run_id=%s",
    (created.record["run_id"],),
)
cursor.execute(
    "UPDATE run_operation SET state='leased', lease_owner=%s, "
    "lease_expires_at=now()+interval '10 minutes' WHERE run_id=%s AND kind='detect'",
    ("00000000-0000-4000-8000-000000000099", created.record["run_id"]),
)
connection.commit(); cursor.close(); connection.close()
interactive = InteractiveRepository(database)
connection = connect(); cursor = connection.cursor()
cursor.execute("SELECT operation_id FROM run_operation WHERE run_id=%s AND kind='detect'", (created.record["run_id"],))
operation_id = str(cursor.fetchone()[0])
cursor.close(); connection.close()
detection = DetectionWork(
    operation_id,
    created.record["run_id"],
    upload["object_name"],
    7,
    "image/jpeg",
    "f" * 64,
    "00000000-0000-4000-8000-000000000099",
)
group_id = "00000000-0000-4000-8000-000000000088"
assert interactive.complete_detection(
    detection,
    [{
        "group_id": group_id,
        "local_group_id": 0,
        "bbox": [0, 0, 10, 10],
        "start_ms": None,
        "end_ms": None,
        "quality": {"detector_confidence": .9},
        "preview_object_name": f"submissions-temporary/{created.record['run_id']}/previews/{group_id}.jpg",
        "preview_generation": 8,
        "embedding": embedding,
    }],
    detector_version="detector",
    embedding_version="embedding",
)
groups, selection_version = runs.groups(created.record["run_id"])
assert groups[0]["group_id"] == group_id
assert runs.select(created.record["run_id"], [group_id], selection_version)["state"] == "matching"
owner, selected = interactive.claim_matching(created.record["run_id"])
rankings = interactive.rank(selected, "embedding", 10)
assert interactive.complete_matching(created.record["run_id"], owner, selected, rankings)
managed_results = runs.results(created.record["run_id"])
assert managed_results["groups"][0]["candidates"][0]["subject_id"] == ranked[0][0].subject_id
assert managed_results["groups"][0]["candidates"][0]["representative_faces"] == []

# Representative publication is publish-then-retire and immediately becomes visible
# through opaque result references without changing the candidate snapshot.
subject_id = ranked[0][0].subject_id
source_id = ranked[0][0].observations[0]["source_id"]
track_id = ranked[0][0].observations[0]["track_id"]
first_face, second_face = str(uuid.uuid4()), str(uuid.uuid4())
connection = connect(); cursor = connection.cursor()
for face_id, generation, quality in ((first_face, 10, .8), (second_face, 11, .9)):
    cursor.execute(
        "INSERT INTO subject_representative_face "
        "(representative_id,subject_id,source_id,source_track_id,object_name,object_generation,content_type,quality_score) "
        "VALUES (%s,%s,%s,%s,%s,%s,'image/jpeg',%s)",
        (face_id, subject_id, source_id, track_id, f"subject-gallery/{subject_id}/{face_id}.jpg", generation, quality),
    )
connection.commit(); cursor.close(); connection.close()
backfill = BackfillRepository(database)
backfill.publish(subject_id, [first_face])
assert runs.results(created.record["run_id"])["groups"][0]["candidates"][0]["representative_faces"][0]["representative_id"] == first_face
backfill.publish(subject_id, [second_face])
connection = connect(); cursor = connection.cursor()
cursor.execute("SELECT active FROM subject_representative_face WHERE representative_id=%s", (second_face,))
assert cursor.fetchone() == [True]
cursor.execute("SELECT object_generation FROM gallery_cleanup_object WHERE representative_id=%s", (first_face,))
assert cursor.fetchone() == [10]
cursor.close(); connection.close()

# Cancellation is immediate when no worker owns the operation and cooperative while a
# detector lease is active. Exact object generations are queued for cleanup.
cancelled = runs.create("member@example.com", "cancel-immediate-01", upload_payload)
runs.prepare_upload(cancelled.record["run_id"])
runs.finalize_upload(
    cancelled.record["run_id"],
    expected_bytes=123,
    expected_sha256="e" * 64,
    object_generation=17,
    object_bytes=123,
)
assert runs.cancel(cancelled.record["run_id"])["state"] == "cancelled"
connection = connect(); cursor = connection.cursor()
cursor.execute(
    "SELECT object_generation FROM run_cleanup_object WHERE run_id=%s",
    (cancelled.record["run_id"],),
)
cleanup_generation = cursor.fetchone()
assert cleanup_generation == [17], cleanup_generation
cursor.close(); connection.close()

cooperative = runs.create("member@example.com", "cancel-cooperative-01", upload_payload)
runs.prepare_upload(cooperative.record["run_id"])
runs.finalize_upload(
    cooperative.record["run_id"],
    expected_bytes=123,
    expected_sha256="d" * 64,
    object_generation=18,
    object_bytes=123,
)
connection = connect(); cursor = connection.cursor()
cursor.execute("UPDATE media_run SET state='detecting' WHERE run_id=%s", (cooperative.record["run_id"],))
cursor.execute(
    "UPDATE run_operation SET state='leased', lease_owner=%s, "
    "lease_expires_at=now()+interval '10 minutes' WHERE run_id=%s AND kind='detect'",
    ("00000000-0000-4000-8000-000000000077", cooperative.record["run_id"]),
)
connection.commit(); cursor.close(); connection.close()
assert runs.cancel(cooperative.record["run_id"])["state"] == "detecting"
connection = connect(); cursor = connection.cursor()
cursor.execute("SELECT cancel_requested FROM media_run WHERE run_id=%s", (cooperative.record["run_id"],))
assert cursor.fetchone() == [True]
cursor.close(); connection.close()

print("database transaction, probe immutability, Phase 1 lifecycle, cancellation, ranking, and gallery publication integration passed")
PY
