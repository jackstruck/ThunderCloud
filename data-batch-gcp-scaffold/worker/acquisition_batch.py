"""Checkpointed cloud acquisition and archive handoff for an explicit video list."""
from __future__ import annotations

import base64
import json
import os
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

from bulk_download.cli import run as acquire
from bulk_download.config import Config, GcpConfig, HttpConfig
from bulk_download.fetch import Fetcher
from bulk_download.hotscope import discover_users, video_id
from google.cloud import storage

from .config import Settings
from .db import Database
from .import_queue import ImportQueue, QueueFull
from .job_invoker import CloudRunJobInvoker
from .operator_submission import validate_payload
from .run_repository import request_fingerprint
from .storage import load_configured_csek


def acquired_sources(rows, selected):
    canonical = {row['object']: row for row in rows if row.get('status') == 'complete'}
    acquired, failures = {}, set()
    for row in rows:
        ident = row.get('provider_id')
        if row.get('provider') != 'hotscope' or ident not in selected:
            continue
        if row['status'] == 'complete':
            acquired[ident] = row
        elif row['status'] == 'duplicate' and row.get('duplicate_of') in canonical:
            acquired[ident] = {**canonical[row['duplicate_of']], 'page_url': row['page_url']}
        elif row['status'] == 'failed':
            failures.add(ident)
    return acquired, failures - set(acquired)


def source_payload(row, policy):
    url = urlsplit(row['object'])
    payload = {'handling_policy': policy, 'selection_policy': 'all_tracks', 'source': {
        'kind': 'archive', 'bucket': url.netloc, 'object_name': url.path.lstrip('/'),
        'generation': int(row['generation']), 'sha256': row['sha256'],
        'bytes': row['bytes'], 'content_type': row['content_type'],
        'page_url': row.get('page_url') or row.get('luluvid_url')}}
    validate_payload(payload)
    return payload


def identity(row):
    return f"{row['object']}#{int(row['generation'])}"


def existing_acquisitions(cursor, policy, rows):
    identities = [(urlsplit(row['object']).netloc, urlsplit(row['object']).path.lstrip('/'),
                   int(row['generation'])) for row in rows]
    if not identities:
        return {}
    buckets, names, generations = zip(*identities)
    cursor.execute("""SELECT r.archive_bucket,r.archive_object_name,r.archive_object_generation,r.run_id,r.state
        FROM media_run r JOIN unnest(%s::text[],%s::text[],%s::bigint[]) wanted(bucket,name,generation)
          ON r.archive_bucket=wanted.bucket AND r.archive_object_name=wanted.name
          AND r.archive_object_generation=wanted.generation
        WHERE r.source_kind='archive' AND r.handling_policy=%s ORDER BY r.created_at,r.run_id""",
        (list(buckets),list(names),list(generations),policy))
    return {f'gs://{b}/{o}#{int(g)}': (str(r), status) for b,o,g,r,status in cursor.fetchall()}


class ManifestProgress:
    """Tail the append-only downloader manifest, retaining parsed progress in memory."""
    def __init__(self, path):
        self.path, self.offset, self.text = path, 0, ''
        self.canonical, self.successes, self.failures = {}, {}, set()
        self.completed_urls, self.completed_hashes = set(), {}

    def refresh(self, selected):
        with self.path.open() as handle:
            handle.seek(self.offset)
            added = handle.read()
            self.offset = handle.tell()
        self.text += added
        for line in added.splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            url = row.get('page_url') or row.get('luluvid_url')
            if row.get('status') in {'complete', 'duplicate'} and isinstance(url, str):
                self.completed_urls.add(url)
            digest = row.get('sha256')
            if (row.get('status') == 'complete' and isinstance(digest, str)
                    and len(digest) == 64 and isinstance(row.get('object'), str)):
                self.completed_hashes.setdefault(digest, row)
            if row.get('status') == 'complete':
                self.canonical[row['object']] = row
            ident = row.get('provider_id')
            if row.get('provider') != 'hotscope':
                continue
            if row['status'] in {'complete','duplicate'}:
                self.successes[ident] = row
            elif row['status'] == 'failed':
                self.failures.add(ident)
        acquired = {}
        for ident, row in self.successes.items():
            if ident not in selected:
                continue
            if row['status'] == 'complete':
                acquired[ident] = row
            elif row.get('duplicate_of') in self.canonical:
                acquired[ident] = {**self.canonical[row['duplicate_of']], 'page_url':row['page_url']}
        return acquired, (self.failures & selected) - set(acquired)


def handoff_key(batch_id, policy, ident):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, batch_id + ':' + policy + ':' + ident))


def record_source_urls(database, rows):
    """Keep alternate URLs even when the archive handoff is skipped as a duplicate."""
    urls = sorted({(row['sha256'],row.get('page_url') or row.get('luluvid_url')) for row in rows
                   if row.get('page_url') or row.get('luluvid_url')})
    if not urls:
        return
    connection = database.connect()
    try:
        cursor = connection.cursor()
        cursor.executemany("INSERT INTO source_url(source_sha256,page_url) VALUES (%s,%s) ON CONFLICT DO NOTHING", urls)
        connection.commit()
    finally:
        connection.close()


def discover_profile(state, http_config, *, fetcher_factory=Fetcher, discover=discover_users):
    with fetcher_factory(http_config) as fetcher:
        found, errors = discover(fetcher, state['users'], set(), None, 1000)
    state['discovery_failures'] = [error.code for error in errors]
    if any(not error.context.get('provider_id') for error in errors):
        raise ValueError('Could not finish discovering every video in this profile.')
    state['videos'] = list(dict.fromkeys(item.provider_id for item in found))
    state['discovery_complete'] = True
    return state['videos']


def main():
    os.umask(0o077)
    settings = Settings.from_env()
    key = load_configured_csek(settings.project_id, settings.csek_file, settings.csek_secret)
    root = Path('/tmp/acquisition-batch')
    root.mkdir(exist_ok=True)
    key_path = root / 'csek.base64'
    key_path.write_bytes(base64.b64encode(key))
    blob = storage.Client(project=settings.project_id).bucket(settings.bucket).blob(
        os.environ['FACE_BATCH_STATE_OBJECT'], encryption_key=key)
    blob.reload()
    generation = int(blob.generation)
    state = json.loads(blob.download_as_bytes(if_generation_match=generation))
    if state['format'] != 1 or state['handling_policy'] not in {'enroll_only', 'retain_and_enroll', 'search_then_discard'}:
        raise ValueError('Invalid batch checkpoint')
    selected = list(dict.fromkeys(video_id(value) for value in state['videos']))
    manifest = root / 'manifest.jsonl'
    manifest.write_text(state['manifest'])
    config = Config(root / 'config.toml', GcpConfig(settings.project_id, settings.bucket, 'videos', key_path),
        root/'unused', root/'unused', root/'unused', root/'media', manifest,
        HttpConfig('BulkDownloader/1.0', 10, 60, 5, 3, 2, 5242880,
                   int(os.getenv('FACE_BATCH_MAX_VIDEO_BYTES', '262144000'))),
        8388608, ('video/mp4', 'video/webm', 'video/quicktime'))
    database = Database(settings.cloud_sql_instance, settings.db_user, settings.db_name, settings.cloud_sql_ip_type)
    queue = ImportQueue(database)
    progress = ManifestProgress(manifest)
    invoke = CloudRunJobInvoker(settings.project_id, os.getenv('FACE_REGION', 'us-central1'),
        os.getenv('FACE_INGEST_JOB', 'face-ingest-drain'), env={'FACE_INGEST_MODE': 'drain'})

    def checkpoint():
        nonlocal generation
        progress.refresh(set(selected))
        state['manifest'] = progress.text
        state['updated_at'] = time.time()
        blob.upload_from_string(json.dumps(state), content_type='application/json',
                                if_generation_match=generation, timeout=120)
        generation = int(blob.generation)

    # An execution owns its checkpoint across task retries. Duplicate launches exit.
    execution = os.getenv('CLOUD_RUN_EXECUTION', str(uuid.uuid4()))
    if state.get('execution') and state['execution'] != execution:
        database.close()
        key_path.unlink(missing_ok=True)
        return
    state['execution'] = execution
    checkpoint()
    recorded_urls = set()
    try:
        if state.get('users') and not state.get('discovery_complete'):
            state['status'] = {'phase': 'discovering'}
            checkpoint()
            started = time.monotonic()
            selected = discover_profile(state, config.http)
            state['discovery_seconds'] = round(time.monotonic() - started, 3)
            checkpoint()
        queued = set()
        last_invoked = float('-inf')
        while True:
            acquired, failures = progress.refresh(set(selected))
            if state['handling_policy'] in {'enroll_only','retain_and_enroll'}:
                additions = [row for row in acquired.values() if (row['sha256'],row.get('page_url')) not in recorded_urls]
                record_source_urls(database, additions)
                recorded_urls.update((row['sha256'],row.get('page_url')) for row in additions)
            unique = {identity(row): row for row in acquired.values()}
            capacity_wait = False
            added = 0
            for ident, row in unique.items():
                if ident in queued:
                    continue
                payload = source_payload(row, state['handling_policy'])
                receipt = state['receipts'].get(ident)
                if receipt is None:
                    receipt = {'format': 1, 'principal': state['principal'], 'payload': payload,
                        'fingerprint': request_fingerprint(payload),
                        'idempotency_key': handoff_key(state['batch_id'], state['handling_policy'], ident),
                        'run_id': None}
                    state['receipts'][ident] = receipt
                if receipt['payload'] != payload or receipt['principal'] != state['principal']:
                    raise ValueError('Receipt does not match the selected policy/source')
                try:
                    # The queue row is the durable receipt before any run creation.
                    receipt['queue_id'] = queue.enqueue(state['batch_id'], receipt['principal'],
                        receipt['idempotency_key'], payload)
                except QueueFull:
                    capacity_wait = True
                    break
                queued.add(ident)
                added += 1
            remaining = [ident for ident in selected if ident not in acquired and ident not in failures]
            complete = not remaining and len(queued) == len(unique)
            state['queue_version'] = 1
            state['status'] = {'phase': 'processing' if complete else 'acquiring',
                'selected':len(selected), 'acquired':len(acquired), 'acquisition_failures':len(failures),
                'remaining_downloads':len(remaining), 'unique_objects':len(unique),
                'queued_for_processing':len(queued), 'awaiting_queue':len(unique)-len(queued),
                'acquisition_complete':not remaining, 'handoff_complete':complete}
            checkpoint()
            print(json.dumps(state['status']), flush=True)
            if added and (complete or time.monotonic() - last_invoked >= 60) and not queue.dispatch_running():
                invoke('')
                last_invoked = time.monotonic()
            if complete:
                return
            if capacity_wait:
                time.sleep(30)  # Backpressure at the bounded durable queue, not GPU admission.
            elif remaining:
                options = SimpleNamespace(provider='hotscope', user=[], users_file=None,
                    video_id=[remaining[0]], videos_file=None, max_videos_per_user=None, max_pages=100)
                started = time.monotonic()
                acquire(config, options=options, manifest_state=(
                    progress.completed_urls, progress.completed_hashes))
                print(json.dumps({'event':'video_acquisition_finished',
                    'batch_id':state['batch_id'],'seconds':round(time.monotonic()-started,3)}), flush=True)
                checkpoint()
    except Exception:
        state['status'] = {**state.get('status', {}), 'phase': 'failed',
                           'message': 'The batch could not finish. Submitted runs remain available below.'}
        checkpoint()
        raise
    finally:
        database.close()
        key_path.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
