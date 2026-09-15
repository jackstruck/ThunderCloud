import numpy as np
import pytest

from worker.gallery_correction import _unit


def test_correction_normalizes_weighted_embeddings_and_rejects_zero():
    result = _unit([3.0, 4.0])
    assert np.allclose(result, [0.6, 0.8])
    with pytest.raises(ValueError):
        _unit([0.0, 0.0])
