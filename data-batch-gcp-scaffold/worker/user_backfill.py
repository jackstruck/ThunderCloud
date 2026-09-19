"""One checkpointed profile acquisition per Cloud Run task from a pinned URL list."""
from __future__ import annotations

import hashlib
import json
import os
import uuid

from google.api_core.exceptions import PreconditionFailed
from google.cloud import storage

from .config import Settings
from .profile_batches import profile_name
from .storage import load_configured_csek


def selected_user(content: bytes, expected_sha256: str, task_index: int, task_count: int) -> str:
    if hashlib.sha256(content).hexdigest() != expected_sha256:
        raise ValueError('Backfill input checksum does not match')
    users = list(dict.fromkeys(profile_name(line.strip())
                              for line in content.decode('utf-8-sig').splitlines() if line.strip()))
    if len(users) != task_count or not 0 <= task_index < task_count:
        raise ValueError('Backfill task count/index does not match the unique input users')
    return users[task_index]


def profile_state(backfill_id: str, username: str, principal: str) -> dict:
    batch_id = str(uuid.uuid5(uuid.UUID(backfill_id), f'user:{username}'))
    return {'format': 1, 'batch_id': batch_id, 'principal': principal,
            'handling_policy': 'retain_and_enroll', 'users': [username], 'videos': [],
            'manifest': '', 'receipts': {}, 'status': {'phase': 'queued'},
            'user_backfill_id': backfill_id}


def ensure_profile(blob, expected):
    try:
        blob.upload_from_string(json.dumps(expected), content_type='application/json',
                                if_generation_match=0)
    except PreconditionFailed:
        blob.reload()
        existing = json.loads(blob.download_as_bytes(if_generation_match=int(blob.generation)))
        for field in ('format', 'batch_id', 'principal', 'handling_policy', 'users', 'user_backfill_id'):
            if existing.get(field) != expected[field]:
                raise ValueError('Existing profile checkpoint does not match this backfill') from None
        # A new execution must not silently exit or steal a checkpoint from an old one.
        owner = existing.get('execution')
        if owner and owner != os.environ['CLOUD_RUN_EXECUTION']:
            raise ValueError('Profile checkpoint belongs to another execution; inspect before resuming') from None


def main():
    from .acquisition_batch import main as acquire_profile

    os.umask(0o077)
    settings = Settings.from_env()
    key = load_configured_csek(settings.project_id, settings.csek_file, settings.csek_secret)
    bucket = storage.Client(project=settings.project_id).bucket(settings.bucket)
    input_blob = bucket.blob(os.environ['FACE_USER_BACKFILL_INPUT_OBJECT'], encryption_key=key)
    content = input_blob.download_as_bytes(
        if_generation_match=int(os.environ['FACE_USER_BACKFILL_INPUT_GENERATION']))
    index = int(os.environ['CLOUD_RUN_TASK_INDEX'])
    count = int(os.environ['CLOUD_RUN_TASK_COUNT'])
    username = selected_user(content, os.environ['FACE_USER_BACKFILL_INPUT_SHA256'], index, count)
    state = profile_state(os.environ['FACE_USER_BACKFILL_ID'], username,
                          os.environ['FACE_USER_BACKFILL_PRINCIPAL'])
    prefix = os.environ['FACE_USER_BACKFILL_CHECKPOINT_PREFIX'].rstrip('/')
    blob = bucket.blob(f"{prefix}/profiles/{state['batch_id']}.json", encryption_key=key)
    ensure_profile(blob, state)
    os.environ['FACE_BATCH_STATE_OBJECT'] = blob.name
    print(json.dumps({'event': 'profile_backfill_started', 'task_index': index,
                      'batch_id': state['batch_id'], 'handling_policy': state['handling_policy']}), flush=True)
    acquire_profile()
    print(json.dumps({'event': 'profile_backfill_handoff_complete', 'task_index': index,
                      'batch_id': state['batch_id']}), flush=True)


if __name__ == '__main__':
    main()
