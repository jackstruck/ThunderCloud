import math

import pytest

from worker.aggregate import BestCandidates, l2_normalize, quality_weighted_mean
from worker.models import Candidate


def test_normalize_returns_unit_vector():
    result = l2_normalize([3, 4])
    assert result == pytest.approx([0.6, 0.8])
    assert math.sqrt(sum(x * x for x in result)) == pytest.approx(1)


def test_normalize_rejects_zero_vector():
    with pytest.raises(ValueError):
        l2_normalize([0, 0])


def test_best_candidates_retains_only_highest_quality():
    retained = BestCandidates(2)
    assert retained.add(Candidate(0.2, 1, [1, 0]))
    assert retained.add(Candidate(0.8, 2, [0, 1]))
    assert not retained.add(Candidate(0.1, 3, [1, 1]))
    assert retained.add(Candidate(0.9, 4, [1, 1]))
    assert [x.quality for x in retained.values()] == [0.9, 0.8]


def test_weighted_mean_is_normalized():
    result = quality_weighted_mean(
        [
            Candidate(0.75, 0, [1, 0]),
            Candidate(0.25, 1, [0, 1]),
        ]
    )
    assert result == pytest.approx(l2_normalize([0.75, 0.25]))
