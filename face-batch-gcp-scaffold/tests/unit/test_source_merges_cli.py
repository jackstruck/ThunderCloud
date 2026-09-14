import copy
import json
import uuid

import pytest

from worker.source_merges_cli import apply_plan, plan_sources, save_private


def document():
    source = str(uuid.uuid4())
    groups = [{'operation_id': str(uuid.uuid4()), 'selected': True} for _ in range(2)]
    return {'format_version': 1, 'origin': 'https://console.example', 'plans': [{
        'format_version': 1, 'algorithm': 'complete-linkage-v1', 'source_id': source,
        'threshold': .9, 'groups': groups}]}


def test_cli_lost_response_replays_original_request_and_partial_resume(tmp_path):
    plan = document()
    path, receipt = tmp_path / 'plan.json', tmp_path / 'receipt.json'
    save_private(path, plan)

    class Client:
        origin = plan['origin']
        def __init__(self):
            self.calls = []
            self.committed = set()
            self.lose = True

        def post(self, path, data):
            operation = data['groups'][0]['operation_id']
            # Durable pending request precedes the network operation.
            saved = json.loads(receipt.read_text())['operations'][operation]
            assert saved['request'] == data and saved['outcome']['status'] == 'pending'
            self.calls.append(copy.deepcopy(data))
            self.committed.add(operation)
            if self.lose:
                self.lose = False
                raise OSError('Connection lost after commit')
            return {'outcomes': [{'operation_id': operation, 'status': 'merged'}]}

    client = Client()
    result = apply_plan(client, path, receipt)
    assert [e['outcome']['status'] for e in result['operations'].values()] == ['failed', 'merged']
    resumed = apply_plan(client, path, receipt)
    assert all(e['outcome']['status'] == 'merged' for e in resumed['operations'].values())
    assert len(client.committed) == 2 and len(client.calls) == 3
    assert client.calls[0] == client.calls[-1]
    assert receipt.stat().st_mode & 0o777 == 0o600
    plan['plans'][0]['threshold'] = .8
    save_private(path, plan)
    with pytest.raises(ValueError, match='submitted operation changed'):
        apply_plan(client, path, receipt)


def test_cli_plans_sequential_explicit_sources_and_retains_failures(tmp_path):
    ids = [str(uuid.uuid4()), str(uuid.uuid4())]

    class Client:
        origin = 'https://console.example'
        def __init__(self):
            self.calls = []

        def post(self, path, data):
            self.calls.append(path)
            if ids[0] in path:
                raise ValueError('background_scan_required')
            return {'source_id': ids[1], 'groups': []}

    client = Client()
    output = tmp_path / 'plan.json'
    result = plan_sources(client, ids, .9, output)
    assert len(result['failures']) == len(result['plans']) == 1
    assert client.calls == [f'/api/sources/{sid}/merge-proposals' for sid in ids]
    assert output.stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError, match='already exists'):
        plan_sources(client, ids, .9, output)
