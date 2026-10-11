import itertools
import math
import random
import uuid
from types import SimpleNamespace

import pytest

from worker.source_merges import complete_linkage, configuration, pair_scores
from worker.subject_management import SubjectError


def test_complete_linkage_distinct_groups_chain_dismissals_and_stable_ties():
    scores = {('a', 'b'): .95, ('b', 'c'): .95, ('a', 'c'): .5, ('d', 'e'): .99}
    assert complete_linkage('edcba', scores, .9) == [('a', 'b'), ('d', 'e')]
    del scores['a', 'b']
    assert complete_linkage('abcde', scores, .9) == [('b', 'c'), ('d', 'e')]


def test_cluster_matches_naive_reference():
    rng = random.Random(14)
    for _ in range(30):
        ids = list('abcdefghijk')
        scores = {pair: rng.choice([.5, .7, .9, 1.0]) for pair in itertools.combinations(ids, 2) if rng.random() > .1}
        clusters = [(i,) for i in ids]
        while True:
            candidates = [(min(scores.get(tuple(sorted((a, b))), -math.inf) for a in left for b in right), left, right)
                          for left, right in itertools.combinations(sorted(clusters), 2)]
            eligible = [(-score, left, right) for score, left, right in candidates if score >= .7]
            if not eligible:
                break
            _, left, right = min(eligible)
            clusters.remove(left)
            clusters.remove(right)
            clusters.append(tuple(sorted(left + right)))
        assert complete_linkage(reversed(ids), scores, .7) == sorted(c for c in clusters if len(c) > 1)


@pytest.mark.parametrize('value', [None, True, '0.9', float('nan'), float('inf'), -1.01, 1.01])
def test_threshold_is_explicit_and_finite(value):
    with pytest.raises(SubjectError):
        configuration({'threshold': value})


def test_model_partitions_and_hard_dismissal():
    members = [{'subject_id': sid, 'model_version': model, '_embedding': [1., 0.]}
               for sid, model in [('a', 'm'), ('b', 'm'), ('c', 'other')]]
    assert pair_scores(members, set()) == {('a', 'b'): 1.0}
    assert pair_scores(members, {('a', 'b')}) == {}


@pytest.mark.parametrize('count,automatic,accepted', [
    (400, False, True), (401, False, False),
    (1000, True, True), (1001, True, False),
])
def test_source_loading_has_separate_automatic_limit(count, automatic, accepted):
    from worker.source_merges import SourceMerges, AUTO_MAX_SUBJECTS
    rows = [(str(uuid.uuid4()), '[1]', 1, 'model', 1, None,
             1, True, 1, 0, 1000, True, True) for _ in range(count)]

    class Cursor:
        def execute(self, query, args=()):
            self.query = query
            if 'WITH selected' in query:
                assert args[1] == (1001 if automatic else 401)

        def fetchone(self):
            return ('source',)

        def fetchall(self):
            return rows if 'WITH selected' in self.query else []

    service = SourceMerges(SimpleNamespace(_summaries=lambda c, ids: [
        {'subject_id': sid} for sid in ids]))
    kwargs = {'max_subjects': AUTO_MAX_SUBJECTS} if automatic else {}
    if accepted:
        members, skipped, _ = service._load(Cursor(), 'source', **kwargs)
        assert not members
        assert len(skipped) == count
    else:
        with pytest.raises(SubjectError) as error:
            service._load(Cursor(), 'source', **kwargs)
        assert error.value.code == 'background_scan_required'


@pytest.mark.parametrize('elapsed,expected', [(45, 'completed'), (61, 'manual_review')])
def test_automatic_planning_uses_sixty_second_budget(monkeypatch, elapsed, expected):
    from worker import source_merges
    clock = [100.0]
    monkeypatch.setattr(source_merges.time, 'monotonic', lambda: clock[0])

    class Cursor:
        def execute(self, query, args=()):
            pass

        def fetchone(self):
            return ('0',)

    service = source_merges.SourceMerges(None)

    def load(cursor, source_id, *, max_subjects):
        assert max_subjects == 1000
        clock[0] += elapsed
        cursor.execute('SELECT 1')
        return [], [], set()

    monkeypatch.setattr(service, '_load', load)
    result = service.auto_merge(Cursor(), str(uuid.uuid4()), str(uuid.uuid4()), .4)
    assert result['status'] == expected
