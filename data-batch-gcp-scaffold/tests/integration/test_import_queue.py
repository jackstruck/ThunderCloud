"""Durable queue/admission behavior against isolated PostgreSQL."""
import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_subject_management import (
    database as database,  # noqa: PLC0414 - pytest fixture registration
)
from test_subject_management import (
    db as db,  # noqa: PLC0414 - pytest fixture registration
)
from test_subject_management import sql

from worker.admission import AdmissionPolicy
from worker.import_queue import ImportQueue, QueueFull
from worker.run_repository import RunConflictError, RunRepository

pytestmark = pytest.mark.skipif(not os.getenv('FACE_SUBJECT_TEST_PORT'), reason='isolated PostgreSQL required')


def payload(name='one'):
    return {'handling_policy':'retain_and_enroll', 'selection_policy':'all_tracks', 'source':{
        'kind':'archive','bucket':'test-bucket','object_name':f'videos/{name}.mp4',
        'generation':1,'sha256':'a'*64,'bytes':100,'content_type':'video/mp4',
        'page_url':f'https://hotscope.tv/video/{name}'}}


def enqueue(queue, batch, name='one'):
    return queue.enqueue(batch, 'operator', str(uuid.uuid5(uuid.UUID(batch),name)), payload(name))


def test_handoff_is_idempotent_and_bounded_before_run_creation(db):
    q = ImportQueue(db, AdmissionPolicy(pending=1))
    batch = str(uuid.uuid4())
    ident = enqueue(q,batch)
    assert enqueue(q,batch) == ident
    assert sql(db,'SELECT count(*) FROM media_run')[0][0] == 0
    with pytest.raises(QueueFull):enqueue(q,batch,'two')
    assert q.status(batch,'other') == []


def test_recovery_after_commit_before_receipt_links_existing_run(db):
    q = ImportQueue(db)
    batch = str(uuid.uuid4())
    enqueue(q,batch)
    item = q.claim()
    run = RunRepository(db).create(item[2],item[3],item[4],workload='batch').record
    sql(db,"UPDATE import_queue SET lease_expires_at=now()-interval '1 second'")
    invoked=[]
    assert q.dispatch(invoked.append)==1
    assert invoked==[run['run_id']]
    assert q.status(batch,'operator')[0]['run_id']==run['run_id']
    assert sql(db,'SELECT count(*) FROM media_run')[0][0]==1
    assert q.dispatch(invoked.append)==0


def test_global_capacity_reserves_interactive_slot_and_excludes_review(db):
    q=ImportQueue(db);batch=str(uuid.uuid4())
    for name in ('one','two','three'):enqueue(q,batch,name)
    assert q.dispatch(lambda _:None)==2
    repo=RunRepository(db)
    interactive=repo.create('another-user',str(uuid.uuid4()),payload('interactive')).record
    with pytest.raises(RunConflictError,match='quota'):
        repo.create('third-user',str(uuid.uuid4()),payload('over-capacity'))
    sql(db,"UPDATE media_run SET state='awaiting_face_selection' WHERE workload='batch'")
    assert q.dispatch(lambda _:None)==1
    assert interactive['run_id']


def test_concurrent_dispatch_cannot_overbook_capacity(db):
    q=ImportQueue(db);batch=str(uuid.uuid4())
    for n in range(8):enqueue(q,batch,str(n))
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(lambda _:q.dispatch(lambda _:None),range(4)))
    assert sql(db,"SELECT count(*) FROM media_run WHERE workload='batch'")[0][0]==2
    assert sql(db,"SELECT count(*) FROM import_queue WHERE state='submitted'")[0][0]==2


def test_submission_failure_is_bounded_and_stale_owner_cannot_finish(db):
    q=ImportQueue(db);batch=str(uuid.uuid4());enqueue(q,batch)
    item=q.claim()
    q.release(item,capacity=True)
    assert sql(db,'SELECT attempts FROM import_queue')[0][0]==0
    for _ in range(5):
        sql(db,'UPDATE import_queue SET available_at=now()')
        item=q.claim();q.release(item)
    assert q.status(batch,'operator')[0]['state']=='failed'
    assert q.claim() is None


def test_archive_reuse_across_profiles(db):
    q=ImportQueue(db);first,second=str(uuid.uuid4()),str(uuid.uuid4())
    enqueue(q,first);q.dispatch(lambda _:None)
    enqueue(q,second);q.dispatch(lambda _:None)
    assert q.status(first,'operator')[0]['run_id']==q.status(second,'operator')[0]['run_id']
    assert sql(db,'SELECT count(*) FROM media_run')[0][0]==1


def test_dispatch_lease_serializes_drains_and_recovers_expired_owner(db):
    q=ImportQueue(db)
    owner=q.acquire_dispatch()
    assert q.dispatch_running()
    assert owner and q.acquire_dispatch() is None
    q.release_dispatch(str(uuid.uuid4()))
    assert q.acquire_dispatch() is None
    sql(db,"UPDATE import_dispatch_lease SET lease_expires_at=now()-interval '1 second'")
    assert not q.dispatch_running()
    replacement=q.acquire_dispatch()
    assert replacement and replacement!=owner
    q.release_dispatch(owner)
    assert q.acquire_dispatch() is None
    q.release_dispatch(replacement)
    assert q.acquire_dispatch()
    sql(db,'UPDATE import_dispatch_lease SET lease_owner=NULL')


def test_reconciliation_recovers_lost_gpu_invocation(db):
    from worker.ingest_repository import IngestRepository
    repo=RunRepository(db)
    run=repo.create('operator',str(uuid.uuid4()),payload()).record['run_id']
    sql(db,"UPDATE media_run SET state='queued' WHERE run_id=%s",(run,))
    sql(db,"INSERT INTO run_operation(run_id,kind,state,updated_at) VALUES (%s,'detect','queued',now()-interval '10 minutes')",(run,))
    ingest=IngestRepository(db)
    assert ingest.recover_processing()==[(run,'detect')]
    assert ingest.recover_processing()==[]
