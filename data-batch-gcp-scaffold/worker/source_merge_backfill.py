"""One-pass historical source matching, excluding sources with merge history."""
from __future__ import annotations

import json
import os
import uuid
from collections import Counter
from datetime import datetime

from google.api_core.exceptions import NotFound

from .config import Settings
from .db import Database
from .source_merges import SourceMerges
from .storage import StorageRepository, load_configured_csek
from .subject_management import SubjectManagement


def references(value):
    result = {key: set() for key in ('subjects', 'examples', 'representatives', 'sources')}
    def visit(item):
        if isinstance(item, list):
            for child in item: visit(child)
        elif isinstance(item, dict):
            for key, child in item.items():
                kind = {'subject_id':'subjects', 'from_subject_id':'subjects', 'to_subject_id':'subjects',
                        'survivor_id':'subjects', 'example_id':'examples', 'example_ids':'examples',
                        'moved_example_ids':'examples', 'representative_id':'representatives',
                        'source_id':'sources'}.get(key)
                if kind:
                    for ident in child if isinstance(child, list) else [child]:
                        try: result[kind].add(str(uuid.UUID(str(ident))))
                        except ValueError: pass
                visit(child)
    visit(value)
    return result


def merged_sources(cursor):
    """Use immutable example provenance, even after a merge is separated or moved.

    Move-connected subjects are conservatively protected when old merge snapshots
    cannot enumerate every original destination example. Failed merges do not count.
    """
    cursor.execute("SELECT subject_id,merged_into_subject_id FROM subject WHERE merged_into_subject_id IS NOT NULL")
    subjects = {str(ident) for row in cursor.fetchall() for ident in row}
    examples, representatives, sources = set(), set(), set()
    cursor.execute("SELECT action,details,result FROM subject_change_event WHERE action IN ('combine','move') ORDER BY created_at")
    moves = []
    def include(refs):
        subjects.update(refs['subjects']); examples.update(refs['examples'])
        representatives.update(refs['representatives']); sources.update(refs['sources'])
    for action, details, result in cursor.fetchall():
        if details.get('failed_before_mutation') or result.get('source_merge_failure'):
            continue
        refs = references([details, result])
        if action == 'combine': include(refs)
        else: moves.append(refs)
    while True:
        related = [r for r in moves if r['subjects'] & subjects]
        if not related: break
        for refs in related:
            include(refs)
            moves.remove(refs)
    cursor.execute('''SELECT source_id FROM subject_example WHERE subject_id=ANY(%s::uuid[])
        UNION SELECT source_id FROM subject_example WHERE example_id=ANY(%s::uuid[])
        UNION SELECT e.source_id FROM subject_representative_face r
            JOIN subject_example e USING(example_id) WHERE r.representative_id=ANY(%s::uuid[])''',
        (sorted(subjects), sorted(examples), sorted(representatives)))
    sources.update(str(row[0]) for row in cursor.fetchall())
    return sources


class HistoricalMatching:
    def __init__(self, database, *, scan_budget_seconds=10, retry_from_backfill_id=None):
        self.subjects = SubjectManagement(database)
        self.scan_budget_seconds = scan_budget_seconds
        self.retry_from_backfill_id = retry_from_backfill_id
        self._history_version = None
        self._protected_sources = None

    def protected_sources(self, cursor):
        # Our own merges are strictly source-local and atomically mark that source
        # as processed. They cannot change eligibility of an untouched source.
        # Every other combine/move invalidates this cache, checked under the lock.
        cursor.execute("""SELECT count(*),max(created_at) FROM subject_change_event
            WHERE action IN ('combine','move') AND actor <> 'historical-source-matching'""")
        version = tuple(cursor.fetchone())
        if version != self._history_version or self._protected_sources is None:
            self._protected_sources = merged_sources(cursor)
            self._history_version = version
        return self._protected_sources

    def inventory(self, cutoff):
        with self.subjects.transaction() as cursor:
            protected = merged_sources(cursor)
            cursor.execute('''SELECT s.source_id,s.metadata,count(DISTINCT e.subject_id)
                FROM source_asset s JOIN subject_example e USING(source_id)
                WHERE s.deleted_at IS NULL AND s.created_at < %s
                GROUP BY s.source_id HAVING count(DISTINCT e.subject_id)>1 ORDER BY s.source_id''', (cutoff,))
            selected, excluded = [], []
            for sid, metadata, count in cursor.fetchall():
                sid = str(sid)
                reason = ('prior_merge' if sid in protected else 'already_matched'
                          if (metadata or {}).get('automatic_matching') or (metadata or {}).get('historical_matching') else None)
                if reason: excluded.append({'source_id':sid,'reason':reason})
                else: selected.append({'source_id':sid,'subjects_before':count})
            return {'sources':selected, 'excluded':excluded}

    def apply_source(self, source_id, backfill_id, cutoff):
        with self.subjects.transaction(write=True) as cursor:
            cursor.execute('SELECT metadata,deleted_at,created_at FROM source_asset WHERE source_id=%s', (source_id,))
            row = cursor.fetchone()
            if not row or row[1] is not None or row[2] >= cutoff:
                return {'status':'skipped','reason':'source_unavailable_or_new'}
            metadata = row[0] or {}
            previous = metadata.get('historical_matching')
            retryable = (previous and self.retry_from_backfill_id
                and previous.get('backfill_id') == self.retry_from_backfill_id
                and previous.get('status') == 'manual_review'
                and previous.get('reason') == 'background_scan_required'
                and not previous.get('outcomes'))
            if previous and not retryable:
                return previous
            if self.retry_from_backfill_id and not retryable:
                return {'status':'skipped','reason':'retry_report_changed'}
            if metadata.get('automatic_matching'):
                return {'status':'skipped','reason':'already_matched'}
            if source_id in self.protected_sources(cursor):
                return {'status':'skipped','reason':'prior_merge'}
            cursor.execute('SELECT count(DISTINCT subject_id),count(*) FROM subject_example WHERE source_id=%s', (source_id,))
            before, examples_before = cursor.fetchone()
            operation_scope = str(uuid.uuid5(uuid.UUID(backfill_id), source_id))
            report = SourceMerges(self.subjects).auto_merge(cursor, source_id, operation_scope, .5,
                actor='historical-source-matching', scan_budget_seconds=self.scan_budget_seconds)
            cursor.execute('SELECT count(DISTINCT subject_id),count(*) FROM subject_example WHERE source_id=%s', (source_id,))
            after, examples_after = cursor.fetchone()
            if examples_before != examples_after:
                raise RuntimeError('Historical matching changed the example count')
            report.update(backfill_id=backfill_id, source_id=source_id, scan_budget_seconds=self.scan_budget_seconds,
                          retry_from_backfill_id=self.retry_from_backfill_id, subjects_before=before,
                          subjects_after=after, examples_preserved=examples_after)
            cursor.execute("UPDATE source_asset SET metadata=jsonb_set(COALESCE(metadata,'{}'::jsonb),'{historical_matching}',%s::jsonb) WHERE source_id=%s",
                (json.dumps(report), source_id))
            return report


def main():
    backfill_id = str(uuid.UUID(os.environ['FACE_BACKFILL_ID']))
    cutoff = datetime.fromisoformat(os.environ['FACE_BACKFILL_CUTOFF'])
    if cutoff.tzinfo is None: raise ValueError('Cutoff must include a timezone')
    apply = os.getenv('FACE_BACKFILL_APPLY', 'false').lower() == 'true'
    scan_budget_seconds = int(os.getenv('FACE_BACKFILL_SCAN_BUDGET_SECONDS', '10'))
    if not 1 <= scan_budget_seconds <= 300: raise ValueError('Scan budget must be 1–300 seconds')
    retry_from = os.getenv('FACE_BACKFILL_RETRY_FROM')
    if retry_from: retry_from = str(uuid.UUID(retry_from))
    settings = Settings.from_env()
    key = load_configured_csek(settings.project_id, settings.csek_file, settings.csek_secret)
    storage = StorageRepository(settings.project_id, settings.bucket, settings.source_prefix, settings.staging_prefix, key)
    blob = storage.bucket.blob(os.environ['FACE_BACKFILL_REPORT_OBJECT'], encryption_key=key)
    database = Database(settings.cloud_sql_instance, settings.db_user, settings.db_name, settings.cloud_sql_ip_type)
    try:
        service = HistoricalMatching(database, scan_budget_seconds=scan_budget_seconds, retry_from_backfill_id=retry_from)
        try:
            blob.reload(); generation = int(blob.generation)
            state = json.loads(blob.download_as_bytes(if_generation_match=generation))
            if (state['backfill_id'] != backfill_id or state['cutoff'] != cutoff.isoformat()
                or state.get('scan_budget_seconds', 10) != scan_budget_seconds
                or state.get('retry_from_backfill_id') != retry_from):
                raise ValueError('Backfill report scope does not match this execution')
        except NotFound:
            if retry_from: raise ValueError('Retry requires an explicit saved source inventory') from None
            state = {'backfill_id':backfill_id, 'cutoff':cutoff.isoformat(), 'threshold':.5, 'scan_budget_seconds':scan_budget_seconds,
                     **service.inventory(cutoff), 'outcomes':{}}
            generation = 0
        def checkpoint():
            nonlocal generation
            blob.upload_from_string(json.dumps(state), content_type='application/json', if_generation_match=generation)
            generation = int(blob.generation)
        checkpoint()
        if apply:
            for source in state['sources']:
                sid = source['source_id']
                if sid in state['outcomes'] and state['outcomes'][sid]['status'] != 'error': continue
                try:
                    state['outcomes'][sid] = service.apply_source(sid, backfill_id, cutoff)
                except Exception as error:  # noqa: BLE001 - persist per-source errors for safe retry
                    state['outcomes'][sid] = {'status':'error','error_type':type(error).__name__}
                checkpoint()
                print(json.dumps({'source_id':sid, **state['outcomes'][sid]}), flush=True)
        state['summary'] = {'selected_sources':len(state['sources']), 'excluded_sources':len(state['excluded']),
            'source_outcomes':dict(Counter(r['status'] for r in state['outcomes'].values())),
            'groups_merged':sum(g['status']=='merged' for r in state['outcomes'].values() for g in r.get('outcomes',[])),
            'subjects_combined':sum(r.get('subjects_before',0)-r.get('subjects_after',0) for r in state['outcomes'].values())}
        checkpoint()
        print(json.dumps(state['summary']), flush=True)
        if any(r['status']=='error' for r in state['outcomes'].values()):
            raise RuntimeError('Some historical sources require retry; see the persisted report')
    finally:
        database.close()


if __name__ == '__main__':
    main()
