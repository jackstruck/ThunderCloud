from fractions import Fraction
from types import SimpleNamespace

from worker.video import frame_timestamp_ms


def test_frame_timestamp_prefers_explicit_time():
    frame = SimpleNamespace(time=1.25, pts=999)
    stream = SimpleNamespace(time_base=Fraction(1, 1000))
    assert frame_timestamp_ms(frame, stream, 0) == 1250


def test_frame_timestamp_uses_pts_then_fallback():
    stream = SimpleNamespace(time_base=Fraction(1, 1000))
    assert frame_timestamp_ms(SimpleNamespace(time=None, pts=250), stream, 0) == 250
    assert frame_timestamp_ms(SimpleNamespace(time=None, pts=None), stream, 375) == 375
