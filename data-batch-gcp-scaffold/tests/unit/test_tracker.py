import pytest

from worker.tracker import _iou


def test_iou():
    assert _iou((0, 0, 10, 10), (0, 0, 10, 10)) == pytest.approx(1)
    assert _iou((0, 0, 2, 2), (3, 3, 4, 4)) == 0
