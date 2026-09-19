"""Shared processing capacity and bounded waiting-room policy."""
import os
from dataclasses import dataclass

ACTIVE_STATES = ('fetching', 'queued', 'detecting', 'matching', 'enrolling')
ADMISSION_LOCK = 8675310


@dataclass(frozen=True)
class AdmissionPolicy:
    active: int = 3
    interactive_reserved: int = 1
    pending: int = 1000
    review: int = 50

    @classmethod
    def from_env(cls, active=None):
        active = int(os.getenv('FACE_ACTIVE_RUN_LIMIT', '3')) if active is None else active
        policy = cls(
            active,
            int(os.getenv('FACE_INTERACTIVE_RESERVED', str(min(1, active - 1)))),
            int(os.getenv('FACE_IMPORT_PENDING_LIMIT', '1000')),
            int(os.getenv('FACE_PENDING_REVIEW_LIMIT', '50')),
        )
        if (policy.active < 1 or not 0 <= policy.interactive_reserved < policy.active
                or policy.pending < 1 or policy.review < 1):
            raise ValueError('Invalid processing admission limits')
        return policy

    @staticmethod
    def lock(cursor):
        cursor.execute('SELECT pg_advisory_xact_lock(%s)', (ADMISSION_LOCK,))

    def full(self, cursor, workload):
        cursor.execute('''SELECT count(*),count(*) FILTER (WHERE workload='batch')
            FROM media_run WHERE state=ANY(%s::text[])''', (list(ACTIVE_STATES),))
        total, batch = cursor.fetchone()
        return total >= self.active or (
            workload == 'batch' and batch >= self.active - self.interactive_reserved)

    def waiting_full(self, cursor, principal, state):
        cursor.execute('SELECT count(*) FROM media_run WHERE submitter_principal=%s AND state=%s',
                       (principal, state))
        return cursor.fetchone()[0] >= self.review

    def review_full(self, cursor, principal):
        cursor.execute('''SELECT count(*) FROM media_run WHERE submitter_principal=%s
            AND selection_policy='manual' AND state NOT IN ('succeeded','failed','cancelled','expired')''',
            (principal,))
        return cursor.fetchone()[0] >= self.review
