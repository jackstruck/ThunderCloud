import hashlib
import json
import uuid

import pytest
from google.api_core.exceptions import PreconditionFailed

from worker.user_backfill import ensure_profile, profile_state, selected_user

CONTENT = b'https://hotscope.tv/user/Mary\nhttps://www.hotscope.tv/user/Mary/\nhttps://hotscope.tv/user/Alice\n'
SHA = hashlib.sha256(CONTENT).hexdigest()


def test_tasks_cover_unique_profiles_and_validate_pinned_input():
    assert selected_user(CONTENT, SHA, 0, 2) == 'Mary'
    assert selected_user(CONTENT, SHA, 1, 2) == 'Alice'
    for digest, index, count in [('bad', 0, 2), (SHA, 2, 2), (SHA, -1, 2), (SHA, 0, 3)]:
        with pytest.raises(ValueError):
            selected_user(CONTENT, digest, index, count)


def test_invalid_profile_rejects_entire_list():
    content = CONTENT + b'https://other.example/user/Invalid\n'
    with pytest.raises(ValueError):
        selected_user(content, hashlib.sha256(content).hexdigest(), 0, 3)


def test_batch_ids_are_stable_across_retries_and_distinct_per_user():
    ident = str(uuid.uuid4())
    first = profile_state(ident, 'Mary', 'operator')
    assert first == profile_state(ident, 'Mary', 'operator')
    assert first['batch_id'] != profile_state(ident, 'Alice', 'operator')['batch_id']
    assert first['handling_policy'] == 'retain_and_enroll'


class ExistingBlob:
    generation = 7

    def __init__(self, state):
        self.state = state

    def upload_from_string(self, *args, **kwargs):
        assert kwargs['if_generation_match'] == 0
        raise PreconditionFailed('already exists')

    def reload(self):
        pass

    def download_as_bytes(self, **kwargs):
        assert kwargs['if_generation_match'] == 7
        return json.dumps(self.state).encode()


def test_retry_preserves_checkpoint_and_rejects_policy_or_execution_conflicts(monkeypatch):
    monkeypatch.setenv('CLOUD_RUN_EXECUTION', 'current')
    state = profile_state(str(uuid.uuid4()), 'Mary', 'operator')
    existing = {**state, 'execution': 'current', 'receipts': {'already': 'submitted'}}
    ensure_profile(ExistingBlob(existing), state)
    assert existing['receipts'] == {'already': 'submitted'}
    for change in [{'handling_policy': 'enroll_only'}, {'execution': 'other'}]:
        with pytest.raises(ValueError):
            ensure_profile(ExistingBlob({**existing, **change}), state)
