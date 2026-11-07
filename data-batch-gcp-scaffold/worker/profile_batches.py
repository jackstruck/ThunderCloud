"""Durable profile submission checkpoints shared with the cloud acquisition job."""
from __future__ import annotations

import json
import re
import uuid
from urllib.parse import urlsplit

from google.api_core.exceptions import NotFound, PreconditionFailed

from .run_repository import RunConflictError, RunNotFoundError
from .source_adapters import HOTSCOPE_PAGE_HOSTS, hotscope_video_id


def profile_name(url):
    if not isinstance(url, str):
        raise ValueError('Enter a valid user profile link.')  # noqa: TRY004 - invalid request input
    if url.startswith('/user/'):
        url = 'https://hotscope.tv' + url
    parsed = urlsplit(url)
    match = re.fullmatch(r'/user/([A-Za-z0-9_.-]+)/?', parsed.path)
    if (parsed.scheme != 'https' or parsed.hostname not in HOTSCOPE_PAGE_HOSTS
            or parsed.port not in {None, 443} or parsed.username or parsed.password
            or parsed.query or parsed.fragment or not match or match[1] in {'.', '..'}):
        raise ValueError('Enter a valid user profile link.')
    return match[1]



class ProfileBatches:
    def __init__(self, storage, repository, invoke, prefix):
        self.storage, self.repository, self.invoke = storage, repository, invoke
        self.prefix = prefix.rstrip('/')

    def _blob(self, batch_id):
        batch_id = str(uuid.UUID(batch_id))
        return self.storage.bucket.blob(f'{self.prefix}/profiles/{batch_id}.json', encryption_key=self.storage.csek)

    def create(self, principal, key, url, policy):
        username = profile_name(url)
        return self._create(principal, key, [username], [], policy)

    def create_video_list(self, principal, key, urls, policy):
        if not isinstance(urls, list) or not 1 <= len(urls) <= 1000:
            raise ValueError('Provide 1 to 1000 video links.')
        videos = []
        for index, url in enumerate(urls, 1):
            try:
                if not isinstance(url, str) or not url.startswith('https://') or urlsplit(url).query or urlsplit(url).fragment:
                    raise ValueError('Invalid video link')
                videos.append(hotscope_video_id(url))
            except ValueError as error:
                raise ValueError(f'Line {index}: provide a direct Hotscope video link.') from error
        videos = list(dict.fromkeys(videos))
        return self._create(principal, key, [], videos, policy)

    def _create(self, principal, key, users, videos, policy):
        batch_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f'profile-batch:{principal}:{key}'))
        blob = self._blob(batch_id)
        state = {'format': 1, 'batch_id': batch_id, 'principal': principal,
                 'handling_policy': policy, 'users': users, 'videos': videos,
                 'manifest': '', 'receipts': {}, 'status': {'phase': 'queued'}}
        try:
            blob.upload_from_string(json.dumps(state), content_type='application/json', if_generation_match=0)
        except PreconditionFailed:
            state = json.loads(blob.download_as_bytes())
            if (state['users'] != users or state['videos'] != videos or state['handling_policy'] != policy
                    or state['principal'] != principal):
                raise RunConflictError('Idempotency key already used for another batch submission.') from None
        if not state.get('execution'):
            self.invoke(blob.name)
        return {'batch_id': batch_id, 'status_url': f'/batches/{batch_id}'}

    def get(self, principal, batch_id):
        try:
            state = json.loads(self._blob(batch_id).download_as_bytes())
        except NotFound:
            raise RunNotFoundError(batch_id) from None
        if state['principal'] != principal:
            raise RunNotFoundError(batch_id)
        ids = {r['run_id'] for r in state['receipts'].values() if r.get('run_id')}
        ids.update(state.get('preexisting_runs', []))
        status = dict(state.get('status', {}))
        if state.get('queue_version') == 1:
            from .import_queue import ImportQueue
            queued = ImportQueue(self.repository.database).status(batch_id, principal)
            ids.update(item['run_id'] for item in queued if item['run_id'])
            status['awaiting_submission'] = sum(item['state'] in {'pending','leased'} for item in queued)
            status['submission_failures'] = sum(item['state']=='failed' for item in queued)
            status['handed_off_or_preexisting'] = sum(item['state']=='submitted' for item in queued)
            status['processing_complete'] = bool(status.get('handoff_complete')) and all(
                item['state']=='failed' or item['processing_state'] in {'succeeded','failed','cancelled','expired'}
                for item in queued)
        runs = [{'run_id': r['run_id'], 'state': r['state'], 'status_url': r['status_url'], 'source_url': r.get('source_url')}
                for r in self.repository.batch_statuses(sorted(ids))]
        if state.get('queue_version') == 1:
            status['processing_complete'] = status['processing_complete'] and len(runs) == len(ids) and all(
                run['state'] in {'succeeded', 'failed', 'cancelled', 'expired'} for run in runs)
        return {'batch_id': batch_id, 'username': state['users'][0] if state['users'] else None,
                'video_count': len(state['videos']) if not state['users'] else None,
                'status': status, 'runs': runs,
                'discovery_failures': len(state.get('discovery_failures', []))}
