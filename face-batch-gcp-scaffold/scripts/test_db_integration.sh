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

postgres_port="$(docker port "${container_name}" 5432/tcp | awk -F: 'NR==1 {print $NF}')"
FACE_TEST_POSTGRES_PORT="${postgres_port}" "${python_bin}" - <<'PY'
import os

import pg8000

from worker.db import Database, Versions
from worker.manifest import ManifestItem
from worker.models import Candidate, TrackTemplate
from worker.queue import QueueDatabase

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
print("database transaction, idempotency, and durable queue integration passed")
PY
