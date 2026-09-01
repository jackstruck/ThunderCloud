from worker.detector import _nms
from worker.models import Detection


def test_nms_keeps_best_overlapping_detection():
    result = _nms(
        [
            Detection((0, 0, 10, 10), 0.9),
            Detection((1, 1, 10, 10), 0.8),
            Detection((20, 20, 30, 30), 0.7),
        ],
        0.4,
    )
    assert [item.score for item in result] == [0.9, 0.7]
