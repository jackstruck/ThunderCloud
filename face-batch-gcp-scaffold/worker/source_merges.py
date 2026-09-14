"""Bounded, source-scoped complete-linkage proposals and transactional application."""
from __future__ import annotations

import heapq
import itertools
import json
import math
import time
import uuid

import numpy as np

from .subject_management import SubjectError, normalized, subject_uuid

ALGORITHM = 'complete-linkage-v1'
FORMAT_VERSION = 1
MAX_SUBJECTS = 400
MAX_COMPARISONS = MAX_SUBJECTS * (MAX_SUBJECTS - 1) // 2


def scan_budget_error():
    return SubjectError(422, 'background_scan_required',
                        'The 10-second source scan budget was exceeded. A bounded background scan is required; no partial plan was returned.')


class PlanningCursor:
    def __init__(self, cursor, deadline):
        self.cursor, self.deadline = cursor, deadline

    def execute(self, statement, args=()):
        remaining = int((self.deadline - time.monotonic()) * 1000)
        if remaining <= 0:
            raise scan_budget_error()
        self.cursor.execute(f'SET LOCAL statement_timeout={remaining}')
        return self.cursor.execute(statement, args)

    def fetchone(self):
        return self.cursor.fetchone()

    def fetchall(self):
        return self.cursor.fetchall()


def configuration(data):
    if not isinstance(data, dict) or set(data) != {'threshold'}:
        raise SubjectError(422, 'invalid_configuration', 'Provide an explicitly reviewed cosine threshold.')
    value = data['threshold']
    if type(value) not in (int, float) or not math.isfinite(value) or not -1 <= value <= 1:
        raise SubjectError(422, 'invalid_threshold', 'Cosine threshold must be a finite number from -1 to 1.')
    return float(value)


def pair_scores(members, dismissed):
    scores = {}
    partitions = {}
    for member in members:
        partitions.setdefault(member['model_version'], []).append(member)
    for partition in partitions.values():
        matrix = np.asarray([m['_embedding'] for m in partition])
        similarities = matrix @ matrix.T
        for i, left in enumerate(partition):
            for j in range(i + 1, len(partition)):
                key = tuple(sorted((left['subject_id'], partition[j]['subject_id'])))
                if key not in dismissed:
                    scores[key] = max(-1.0, min(1.0, float(similarities[i, j])))
    return scores


def complete_linkage(ids, scores, threshold):
    """Highest minimum similarity first; stable member IDs break all ties."""
    clusters = {sid: (sid,) for sid in sorted(ids)}
    distances = dict(scores)
    heap = [(-score, (a,), (b,), a, b) for (a, b), score in scores.items() if score >= threshold]
    heapq.heapify(heap)
    serial = 0
    while heap:
        _negative, left, right, a, b = heapq.heappop(heap)
        if a not in clusters or b not in clusters:
            continue
        merged = tuple(sorted(left + right))
        del clusters[a], clusters[b]
        serial += 1
        key = f'cluster:{serial}'
        for other, members in clusters.items():
            score = min(distances.get(tuple(sorted((a, other))), -math.inf),
                        distances.get(tuple(sorted((b, other))), -math.inf))
            distances[tuple(sorted((key, other)))] = score
            if score >= threshold:
                x, y = sorted(((merged, key), (members, other)))
                heapq.heappush(heap, (-score, x[0], y[0], x[1], y[1]))
        clusters[key] = merged
    return sorted(members for members in clusters.values() if len(members) > 1)


def identity_conflicts(members):
    return [field for field in ('display_name', 'external_identity_ref')
            if len({m[field] for m in members if m.get(field)}) > 1]


class SourceMerges:
    def __init__(self, subjects, *, apply_enabled=False):
        self.subjects = subjects
        self.apply_enabled = apply_enabled

    def _load(self, cursor, source_id, ids=None):
        cursor.execute('SELECT source_id FROM source_asset WHERE source_id=%s', (source_id,))
        if cursor.fetchone() is None:
            raise SubjectError(404, 'source_not_found', 'Source not found.')
        cursor.execute('''SELECT s.subject_id,s.canonical_embedding::text,s.sample_count,s.model_version,
            s.row_version,s.merged_into_subject_id,
            count(DISTINCT e.source_id),bool_and(e.source_id=%s),
            count(e.example_id),min(e.start_ms),max(e.end_ms),bool_and(e.model_version=s.model_version)
            FROM subject s LEFT JOIN subject_example e USING(subject_id)
            WHERE (%s::uuid[] IS NOT NULL AND s.subject_id=ANY(%s::uuid[])) OR
              (%s::uuid[] IS NULL AND s.merged_into_subject_id IS NULL AND EXISTS
                (SELECT 1 FROM subject_example own WHERE own.subject_id=s.subject_id AND own.source_id=%s))
            GROUP BY s.subject_id ORDER BY s.subject_id LIMIT %s''',
            (source_id, ids, ids, ids, source_id, MAX_SUBJECTS + 1))
        rows = cursor.fetchall()
        if len(rows) > MAX_SUBJECTS:
            raise SubjectError(422, 'background_scan_required',
                               f'Source exceeds {MAX_SUBJECTS} subjects / {MAX_COMPARISONS} comparisons; a bounded background scan is required. Nothing was partially scanned.')
        members, skipped = [], []
        summaries = {m['subject_id']: m for m in self.subjects._summaries(cursor, [str(r[0]) for r in rows])}
        for sid, embedding, samples, model, version, merged, sources, same, count, start, end, same_model in rows:
            sid = str(sid)
            reason = None
            if merged is not None:
                reason = 'subject_merged'
            elif sources != 1 or not same:
                reason = 'multi_source' if sources > 1 else 'source_membership_changed'
            elif not same_model:
                reason = 'model_mismatch'
            elif samples != count:
                reason = 'stale_embedding'
            elif not embedding or not samples or not model:
                reason = 'missing_embedding'
            else:
                try:
                    embedding = normalized(json.loads(embedding))
                except (SubjectError, TypeError, ValueError, OverflowError):
                    reason = 'invalid_embedding'
            if reason:
                skipped.append({'subject_id': sid, 'reason': reason})
                continue
            members.append({**summaries[sid], '_embedding': embedding,
                            'source_time_range': {'start_ms': start, 'end_ms': end}})
        member_ids = [m['subject_id'] for m in members]
        cursor.execute('''SELECT d.subject_low,d.subject_high FROM subject_suggestion_dismissal d
            JOIN subject lo ON lo.subject_id=d.subject_low JOIN subject hi ON hi.subject_id=d.subject_high
            WHERE d.subject_low=ANY(%s::uuid[]) AND d.subject_high=ANY(%s::uuid[])
            AND d.low_version=lo.row_version AND d.high_version=hi.row_version AND d.restored_at IS NULL''',
            (member_ids, member_ids))
        dismissed = {tuple(map(str, row)) for row in cursor.fetchall()}
        return members, skipped, dismissed

    def plan(self, source_id, data):
        source_id = subject_uuid(source_id)
        threshold = configuration(data)
        deadline = time.monotonic() + 10
        try:
            with self.subjects.transaction() as cursor:
                members, skipped, dismissed = self._load(PlanningCursor(cursor, deadline), source_id)
        except Exception as error:
            if error.args and isinstance(error.args[0], dict) and error.args[0].get('C') == '57014':
                raise scan_budget_error() from error
            raise
        scores = pair_scores(members, dismissed)
        by_id = {m['subject_id']: m for m in members}
        groups = []
        for ids in complete_linkage(by_id, scores, threshold):
            selected = [by_id[sid] for sid in ids]
            groups.append({
                'operation_id': str(uuid.uuid4()), 'model_version': selected[0]['model_version'],
                'members': [{k: v for k, v in m.items() if not k.startswith('_')} for m in selected],
                'survivor_id': min(selected, key=lambda m: (-m['example_count'], m['subject_id']))['subject_id'],
                'minimum_similarity': min(scores[pair] for pair in itertools.combinations(ids, 2)),
                'identity_conflicts': identity_conflicts(selected), 'review_identity_conflicts': False,
                'selected': False, 'manual_handling_required': len(ids) > 50,
            })
        if time.monotonic() > deadline:
            raise scan_budget_error()
        return {'format_version': FORMAT_VERSION, 'algorithm': ALGORITHM,
                'plan_id': str(uuid.uuid4()), 'source_id': source_id, 'threshold': threshold,
                'apply_enabled': self.apply_enabled, 'groups': groups, 'skipped': skipped,
                'dismissed_pairs': [list(pair) for pair in sorted(dismissed)], 'eligible_subject_count': len(members),
                'model_partitions': [{'model_version': model, 'subject_count': sum(m['model_version'] == model for m in members)}
                                     for model in sorted({m['model_version'] for m in members})],
                'comparison_limit': MAX_COMPARISONS, 'subject_limit': MAX_SUBJECTS}

    def apply(self, source_id, data, actor):
        if not self.apply_enabled:
            raise SubjectError(403, 'proposals_only', 'Rollout is in read-only proposal review. Operator application is not enabled.')
        source_id = subject_uuid(source_id)
        if not isinstance(data, dict) or data.get('format_version') != FORMAT_VERSION or data.get('algorithm') != ALGORITHM or data.get('source_id') != source_id:
            raise SubjectError(422, 'invalid_plan', 'Provide a supported plan for this source.')
        threshold = configuration({'threshold': data.get('threshold')})
        groups = data.get('groups')
        if not isinstance(groups, list) or len(groups) > MAX_SUBJECTS // 2:
            raise SubjectError(422, 'invalid_groups', 'Provide a bounded list of explicitly selected groups.')
        seen, operations = set(), set()
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get('members'), list):
                raise SubjectError(422, 'invalid_group', 'Each group needs member IDs and versions.')
            ids = [subject_uuid(m.get('subject_id')) for m in group['members'] if isinstance(m, dict)]
            op = subject_uuid(group.get('operation_id'))
            if len(ids) != len(group['members']) or len(ids) != len(set(ids)) or seen.intersection(ids) or op in operations:
                raise SubjectError(422, 'overlapping_groups', 'Groups must be disjoint with unique operation IDs.')
            if len(seen | set(ids)) > MAX_SUBJECTS:
                raise SubjectError(422, 'invalid_groups', 'At most 400 members may be submitted per source.')
            seen.update(ids)
            operations.add(op)
        outcomes = []
        for group in groups:
            operation = group['operation_id']
            if group.get('selected') is not True:
                outcomes.append({'operation_id': operation, 'status': 'skipped', 'reason': 'not_selected'})
                continue
            try:
                result = self._apply_group(source_id, threshold, group, actor)
                if 'source_merge_failure' in result:
                    outcomes.append({'operation_id': operation, **result['source_merge_failure']})
                else:
                    outcomes.append({'operation_id': operation, 'status': 'merged', 'result': result})
            except SubjectError as error:
                outcomes.append({'operation_id': operation, 'status': 'stale' if error.status == 409 and error.code != 'operation_conflict' else 'failed',
                                 'code': error.code, 'message': error.message})
            except Exception:  # noqa: BLE001 - isolate uncertain outcomes per atomic group
                # The operation may have committed before a connection failed. Preserve its ID for retry.
                outcomes.append({'operation_id': operation, 'status': 'failed', 'code': 'operation_uncertain',
                                 'message': 'Retry this unchanged group with its original operation ID to resolve the outcome.'})
        return {'source_id': source_id, 'outcomes': outcomes}

    def _apply_group(self, source_id, threshold, group, actor):
        members = group['members']
        if not 2 <= len(members) <= 50:
            raise SubjectError(422, 'manual_handling_required', 'Groups must have 2–50 members; larger groups require manual handling.')
        sid = subject_uuid(group.get('survivor_id'))
        by_id = {subject_uuid(m['subject_id']): m for m in members}
        if sid not in by_id or type(group.get('review_identity_conflicts')) is not bool:
            raise SubjectError(422, 'invalid_survivor', 'Choose a member to keep and explicitly review any identity conflicts.')
        scope = {'format_version': FORMAT_VERSION, 'algorithm': ALGORITHM, 'source_id': source_id,
                 'threshold': threshold, 'model_version': group.get('model_version'),
                 'review_identity_conflicts': group['review_identity_conflicts'],
                 'proposed_minimum_similarity': group.get('minimum_similarity'),
                 'identity_versions': {key: m.get('identity_version') for key, m in by_id.items()}}
        payload = {'operation_id': group['operation_id'], 'version': by_id[sid].get('version'),
                   'subjects': [{'subject_id': key, 'version': by_id[key].get('version')} for key in sorted(by_id) if key != sid]}

        def validate(cursor):
            current, skipped, dismissed = self._load(cursor, source_id, sorted(by_id))
            if skipped or {m['subject_id'] for m in current} != set(by_id):
                raise SubjectError(409, 'source_membership_changed', 'The entire group is stale: a member merged or changed source membership.')
            for member in current:
                expected = by_id[member['subject_id']]
                self.subjects._version(member['version'], expected.get('version'))
                if member['identity_version'] != expected.get('identity_version'):
                    raise SubjectError(409, 'identity_changed', 'Identity details changed; review this group again.')
                if member['model_version'] != group.get('model_version'):
                    raise SubjectError(409, 'model_mismatch', 'The group must use one unchanged recognition model.')
            scores = pair_scores(current, dismissed)
            if any(scores.get(pair, -math.inf) < threshold for pair in itertools.combinations(sorted(by_id), 2)):
                raise SubjectError(409, 'pair_ineligible', 'A pair is dismissed or no longer meets the threshold. Review the entire group.')
            scope['verified_minimum_similarity'] = min(scores.values())
            if identity_conflicts(current) and not group['review_identity_conflicts']:
                raise SubjectError(409, 'identity_conflict', 'Review conflicting identity details and the chosen survivor explicitly.')

        with self.subjects.transaction(write=True) as cursor:
            operation, fingerprint, replay = self.subjects._operation(
                cursor, sid, 'combine', {**payload, 'source_merge': scope}, actor)
            if replay is not None:
                return replay
            cursor.execute('SAVEPOINT source_merge_group')
            try:
                return self.subjects._bulk_combine(cursor, sid, payload, actor, scope=scope, validate=validate)
            except SubjectError as error:
                # Persist definitive failures too: an admitted operation ID binds its
                # payload even when eligibility changed. Unknown commit outcomes still
                # propagate to the caller for an unchanged retry.
                cursor.execute('ROLLBACK TO SAVEPOINT source_merge_group')
                result = {'source_merge_failure': {
                    'status': 'stale' if error.status == 409 else 'failed',
                    'code': error.code, 'message': error.message}}
                self.subjects._record(cursor, operation, actor, 'combine', fingerprint,
                                      {'source_merge': scope, 'failed_before_mutation': True}, result)
                return result
