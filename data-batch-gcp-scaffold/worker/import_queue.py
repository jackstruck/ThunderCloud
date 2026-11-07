"""Idempotent archive handoff with leased, recoverable processing dispatch."""
from __future__ import annotations

import json
import uuid
from contextlib import contextmanager

from .admission import AdmissionPolicy
from .operator_submission import validate_payload
from .run_repository import RunConflictError, RunRepository, request_fingerprint


class QueueFull(RunConflictError):
    pass


class ImportQueue:
    def __init__(self, database, policy=None):
        self.database = database
        self.policy = policy or AdmissionPolicy.from_env()

    def acquire_dispatch(self):
        owner = str(uuid.uuid4())
        with self.transaction() as cursor:
            cursor.execute('INSERT INTO import_dispatch_lease(singleton) VALUES (true) ON CONFLICT DO NOTHING')
            cursor.execute('''UPDATE import_dispatch_lease SET lease_owner=%s,
                lease_expires_at=now()+interval '960 seconds'
                WHERE singleton AND (lease_owner IS NULL OR lease_expires_at<now())
                RETURNING lease_owner''', (owner,))
            return owner if cursor.fetchone() else None

    def release_dispatch(self, owner):
        with self.transaction() as cursor:
            cursor.execute('''UPDATE import_dispatch_lease SET lease_owner=NULL,lease_expires_at=NULL
                WHERE singleton AND lease_owner=%s''', (owner,))

    def has_pending(self):
        with self.transaction() as cursor:
            cursor.execute("SELECT EXISTS (SELECT 1 FROM import_queue WHERE state IN ('pending','leased'))")
            return cursor.fetchone()[0]

    def dispatch_running(self):
        with self.transaction() as cursor:
            cursor.execute('''SELECT EXISTS (SELECT 1 FROM import_dispatch_lease
                WHERE lease_owner IS NOT NULL AND lease_expires_at>now())''')
            return cursor.fetchone()[0]

    @contextmanager
    def transaction(self):
        connection = self.database.connect()
        cursor = connection.cursor()
        try:
            yield cursor
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def enqueue(self, batch_id, principal, key, payload):
        validate_payload(payload)
        fingerprint = request_fingerprint(payload)
        queue_id = str(uuid.uuid5(uuid.NAMESPACE_URL, 'import:' + principal + ':' + key))
        with self.transaction() as cursor:
            self.policy.lock(cursor)
            cursor.execute('''SELECT queue_id,request_fingerprint,batch_id FROM import_queue
                WHERE principal=%s AND idempotency_key=%s''', (principal, key))
            existing = cursor.fetchone()
            if existing:
                if existing[1] != fingerprint or str(existing[2]) != batch_id:
                    raise RunConflictError('Import receipt already used for different work')
                return str(existing[0])
            cursor.execute("SELECT count(*) FROM import_queue WHERE state IN ('pending','leased')")
            if cursor.fetchone()[0] >= self.policy.pending:
                raise QueueFull('import queue capacity reached')
            cursor.execute('''INSERT INTO import_queue
                (queue_id,batch_id,principal,idempotency_key,request_fingerprint,payload)
                VALUES (%s,%s,%s,%s,%s,%s::jsonb)''',
                (queue_id, batch_id, principal, key, fingerprint, json.dumps(payload)))
        return queue_id

    def claim(self):
        owner = str(uuid.uuid4())
        with self.transaction() as cursor:
            self.policy.lock(cursor)
            if self.policy.full(cursor, 'batch'):
                return None
            cursor.execute('''WITH candidate AS (
                SELECT queue_id FROM import_queue
                WHERE (state='pending' AND available_at<=now())
                   OR (state='leased' AND lease_expires_at<now())
                ORDER BY created_at,queue_id FOR UPDATE SKIP LOCKED LIMIT 1
            ) UPDATE import_queue q SET state='leased',lease_owner=%s,
                lease_expires_at=now()+interval '120 seconds',updated_at=now()
              FROM candidate WHERE q.queue_id=candidate.queue_id
              RETURNING q.queue_id,q.principal,q.idempotency_key,q.payload''', (owner,))
            row = cursor.fetchone()
            return None if row is None else (str(row[0]), owner, row[1], row[2], row[3])

    def finish(self, item, run_id):
        queue_id, owner, *_ = item
        with self.transaction() as cursor:
            cursor.execute('''UPDATE import_queue SET state='submitted',run_id=%s,
                submitted_at=now(),updated_at=now(),lease_owner=NULL,lease_expires_at=NULL,last_error_code=NULL
                WHERE queue_id=%s AND state='leased' AND lease_owner=%s''', (run_id, queue_id, owner))

    def release(self, item, *, capacity=False):
        queue_id, owner, *_ = item
        with self.transaction() as cursor:
            cursor.execute('''UPDATE import_queue SET
                attempts=attempts+%s,state=CASE WHEN attempts+%s>=5 THEN 'failed' ELSE 'pending' END,
                last_error_code=%s,available_at=now()+interval '30 seconds',updated_at=now(),
                lease_owner=NULL,lease_expires_at=NULL
                WHERE queue_id=%s AND state='leased' AND lease_owner=%s''',
                (0 if capacity else 1, 0 if capacity else 1,
                 'capacity_wait' if capacity else 'submission_failed', queue_id, owner))

    def status(self, batch_id, principal):
        with self.transaction() as cursor:
            cursor.execute('''SELECT q.queue_id,q.state,q.run_id,r.state,q.last_error_code
                FROM import_queue q LEFT JOIN media_run r USING(run_id)
                WHERE q.batch_id=%s AND q.principal=%s ORDER BY q.created_at,q.queue_id''',
                (batch_id, principal))
            return [{'queue_id':str(q), 'state':state, 'run_id':str(run) if run else None,
                     'processing_state':processing, 'error_code':error}
                    for q,state,run,processing,error in cursor.fetchall()]

    def dispatch(self, invoke, limit=10):
        repository = RunRepository(self.database, active_run_limit=self.policy.active)
        submitted = 0
        for _ in range(limit):
            item = self.claim()
            if item is None:
                break
            _, _, principal, key, payload = item
            try:
                source = payload['source']
                with self.transaction() as cursor:
                    cursor.execute('''SELECT run_id,state FROM media_run WHERE source_kind='archive'
                        AND archive_bucket=%s AND archive_object_name=%s AND archive_object_generation=%s
                        AND handling_policy=%s ORDER BY created_at DESC,run_id DESC LIMIT 1''',
                        (source['bucket'],source['object_name'],source['generation'],payload['handling_policy']))
                    existing = cursor.fetchone()
                if existing and existing[1] not in {'failed', 'cancelled', 'expired'}:
                    run_id = str(existing[0])
                else:
                    run_id = repository.create(principal, key, payload, workload='batch').record['run_id']
                self.finish(item, run_id)
            except RunConflictError as error:
                capacity = str(error) == 'active run quota reached'
                self.release(item, capacity=capacity)
                if capacity:
                    break
                continue
            except Exception:
                self.release(item)
                raise
            # Submission is durable before invocation; reconciliation recovers a
            # lost invocation without creating another logical submission.
            invoke(run_id)
            submitted += 1
        return submitted
