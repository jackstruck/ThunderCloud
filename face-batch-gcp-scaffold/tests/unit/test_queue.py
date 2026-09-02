import uuid
from datetime import UTC, datetime

from worker.queue import QueueDatabase


class Cursor:
    def __init__(self, rows):
        self.rows = iter(rows)
        self.executed = []

    def execute(self, sql, parameters):
        self.executed.append((sql, parameters))

    def fetchone(self):
        return next(self.rows)

    def close(self):
        pass


class Connection:
    def __init__(self, rows):
        self.cursor_value = Cursor(rows)
        self.committed = False

    def cursor(self):
        return self.cursor_value

    def commit(self):
        self.committed = True

    def rollback(self):
        pass

    def close(self):
        pass


class Database:
    def __init__(self, rows):
        self.connection = Connection(rows)

    def connect(self):
        return self.connection


def test_claim_returns_persisted_job_and_unique_lease_owner():
    work_id = uuid.uuid4()
    rollout_id = uuid.uuid4()
    job_id = uuid.uuid4()
    database = Database(
        [
            (
                work_id,
                rollout_id,
                "uid",
                "gs://bucket/videos/a.mp4",
                "a" * 64,
                7,
                99,
                "video/mp4",
                datetime.now(UTC),
                {"uid": "uid"},
                job_id,
                1,
                3,
                None,
                None,
            )
        ]
    )
    item = QueueDatabase(database).claim(str(rollout_id))
    assert item is not None
    assert item.application_job_id == str(job_id)
    uuid.UUID(item.lease_owner)
    assert database.connection.committed
    sql, parameters = database.connection.cursor_value.executed[0]
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert parameters[0] == str(rollout_id)


def test_claim_none_when_queue_is_empty():
    database = Database([None])
    assert QueueDatabase(database).claim(str(uuid.uuid4())) is None
