import itertools
import math
import random

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
