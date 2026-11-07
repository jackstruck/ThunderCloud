import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from google.api_core.exceptions import PreconditionFailed

from worker.profile_batches import ProfileBatches, profile_name
from worker.run_repository import RunConflictError, RunNotFoundError


@pytest.mark.parametrize('value', ['/user/Mary', 'https://hotscope.tv/user/Mary', 'https://www.hotscope.tv/user/Mary/'])
def test_profile_links(value):
    assert profile_name(value) == 'Mary'


@pytest.mark.parametrize('value', ['https://other.test/user/Mary', '/user/..', 'https://hotscope.tv:444/user/Mary', 'https://x@hotscope.tv/user/Mary', 'https://hotscope.tv/user/Mary?q=x', '/user/a/b'])
def test_invalid_profiles(value):
    with pytest.raises(ValueError):
        profile_name(value)


def test_profile_checkpoint_idempotency_policy_and_ownership():
    objects = {}
    class Blob:
        def __init__(self, name): self.name = name
        def upload_from_string(self, text, **kwargs):
            assert kwargs['if_generation_match'] == 0
            if self.name in objects: raise PreconditionFailed('exists')
            objects[self.name] = text
        def download_as_bytes(self): return objects[self.name].encode()
    storage = SimpleNamespace(bucket=SimpleNamespace(blob=lambda name, **_: Blob(name)), csek=b'key')
    repo, invoke = Mock(), Mock()
    repo.batch_statuses.return_value = []
    service = ProfileBatches(storage, repo, invoke, 'batch-control/test')
    result = service.create('tester', 'unique-request-key', '/user/Mary', 'enroll_only')
    assert service.create('tester', 'unique-request-key', '/user/Mary', 'enroll_only') == result
    assert len(objects) == 1
    path = next(iter(objects)); state = json.loads(objects[path]); state['execution'] = 'running'; objects[path] = json.dumps(state)
    invoke.reset_mock()
    service.create('tester', 'unique-request-key', '/user/Mary', 'enroll_only')
    invoke.assert_not_called()
    assert service.get('tester', result['batch_id'])['username'] == 'Mary'
    with pytest.raises(RunConflictError): service.create('tester', 'unique-request-key', '/user/Other', 'enroll_only')
    with pytest.raises(RunNotFoundError): service.get('other', result['batch_id'])


def test_video_link_batch_checkpoint_deduplicates_and_rejects_bad_links():
    objects = {}
    class Blob:
        def __init__(self, name): self.name = name
        def upload_from_string(self, value, **kwargs):
            if self.name in objects: raise PreconditionFailed('exists')
            objects[self.name] = value
        def download_as_bytes(self): return objects[self.name].encode()
    storage = SimpleNamespace(bucket=SimpleNamespace(blob=lambda name, **_: Blob(name)), csek=b'key')
    repo, invoke = Mock(), Mock(); repo.batch_statuses.return_value = []
    service = ProfileBatches(storage, repo, invoke, 'batch-control/test')
    links = ['https://hotscope.tv/video/abc', 'https://www.hotscope.tv/video/abc', 'https://hotscope.tv/video/def']
    result = service.create_video_list('tester', 'video-request-key', links, 'search_then_discard')
    assert service.get('tester', result['batch_id'])['video_count'] == 2
    assert json.loads(next(iter(objects.values())))['videos'] == ['abc', 'def']
    assert service.create_video_list('tester', 'video-request-key', links, 'search_then_discard') == result
    with pytest.raises(RunConflictError): service.create_video_list('tester', 'video-request-key', links[:1], 'search_then_discard')
    with pytest.raises(ValueError, match='Line 2'):
        service.create_video_list('tester', 'different-request-key', [links[0], 'https://hotscope.tv/user/name'], 'search_then_discard')


@pytest.mark.parametrize('queue_state,legacy_state,complete', [
    ('pending', 'succeeded', False),
    ('submitted', 'detecting', False),
    ('submitted', 'succeeded', True),
    ('failed', 'succeeded', True),
])
def test_queue_completion_includes_pending_work_and_legacy_receipts(monkeypatch, queue_state, legacy_state, complete):
    batch_id = 'ed960846-4354-48c7-b4e2-977ea21aa605'
    state = {'principal': 'tester', 'users': ['Mary'], 'queue_version': 1,
             'receipts': {'old': {'run_id': 'old-run'}},
             'status': {'handoff_complete': True}}
    blob = SimpleNamespace(download_as_bytes=lambda: json.dumps(state).encode())
    storage = SimpleNamespace(bucket=SimpleNamespace(blob=lambda *a, **k: blob), csek=b'key')
    repo = Mock()
    repo.batch_statuses.return_value = [
        {'run_id': 'old-run', 'state': legacy_state, 'status_url': '/runs/old-run'}]
    queued = {'run_id': None, 'state': queue_state,
              'processing_state': 'succeeded' if queue_state == 'submitted' else None}
    queue = Mock()
    queue.status.return_value = [queued]
    monkeypatch.setattr('worker.import_queue.ImportQueue', lambda _: queue)
    result = ProfileBatches(storage, repo, Mock(), 'batch-control/test').get('tester', batch_id)
    assert result['status']['processing_complete'] is complete
    assert result['status']['awaiting_submission'] == (queue_state == 'pending')
    assert result['status']['submission_failures'] == (queue_state == 'failed')
