"""Pool lifecycle checks against an isolated local PostgreSQL instance."""
import os
import threading
from concurrent.futures import ThreadPoolExecutor

import pg8000
import pytest
from sqlalchemy.exc import TimeoutError

from worker.db import Database

pytestmark = pytest.mark.skipif(not os.getenv('FACE_SUBJECT_TEST_PORT'), reason='isolated PostgreSQL required')


@pytest.fixture
def pool(monkeypatch):
    monkeypatch.setenv('FACE_DB_POOL_SIZE', '2')
    monkeypatch.setenv('FACE_DB_POOL_TIMEOUT', '0.1')
    db = Database('local', 'postgres', 'postgres')
    db._open = lambda: pg8000.connect(user='postgres', host='127.0.0.1',
        port=int(os.environ['FACE_SUBJECT_TEST_PORT']), database='postgres')
    yield db
    db.close()


def query(connection, statement):
    cursor = connection.cursor()
    try:
        cursor.execute(statement)
        return cursor.fetchall()
    finally:
        cursor.close()


def test_pool_resets_session_and_unfinished_transaction(pool):
    c = pool.connect()
    pid = query(c, 'SELECT pg_backend_pid()')[0][0]
    query(c, "SELECT set_config('application_name','borrower',false)")
    c.commit()
    query(c, "SELECT set_config('statement_timeout','1234',true)")
    c.close()
    c = pool.connect()
    cursor = c.cursor()
    cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
    cursor.close()
    assert query(c, 'SELECT pg_backend_pid()')[0][0] == pid
    assert query(c, 'SHOW application_name')[0][0] != 'borrower'
    assert query(c, 'SHOW statement_timeout')[0][0] == '0'
    c.close()


def test_pool_bounds_and_recovers_after_exhaustion(pool):
    first, second = pool.connect(), pool.connect()
    with pytest.raises(TimeoutError):
        pool.connect()
    first.close()
    replacement = pool.connect()
    assert query(replacement, 'SELECT 1')[0][0] == 1
    replacement.close()
    second.close()


def test_pool_replaces_terminated_connection(pool):
    first, second = pool.connect(), pool.connect()
    pid = query(first, 'SELECT pg_backend_pid()')[0][0]
    first.close()
    query(second, f'SELECT pg_terminate_backend({pid})')
    second.commit()
    replacement = pool.connect()
    assert query(replacement, 'SELECT pg_backend_pid()')[0][0] != pid
    replacement.close()
    second.close()


def test_concurrent_borrowers_have_independent_connections(pool):
    barrier = threading.Barrier(2)
    def borrow(_):
        c = pool.connect()
        try:
            barrier.wait(timeout=5)
            return query(c, 'SELECT pg_backend_pid()')[0][0]
        finally:
            c.close()
    with ThreadPoolExecutor(max_workers=2) as executor:
        assert len(set(executor.map(borrow, range(2)))) == 2
