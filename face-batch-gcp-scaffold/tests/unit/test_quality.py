import pytest

from worker.quality import _pose_symmetry


def test_frontal_landmark_geometry_scores_higher_than_offset_geometry():
    frontal = ((30, 30), (70, 30), (50, 50), (35, 70), (65, 70))
    offset = ((30, 30), (70, 40), (68, 50), (35, 70), (65, 80))
    assert _pose_symmetry(frontal) == pytest.approx(1)
    assert _pose_symmetry(offset) < _pose_symmetry(frontal)
