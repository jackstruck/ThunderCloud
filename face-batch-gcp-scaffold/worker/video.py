from __future__ import annotations

import io
from collections.abc import Iterator


def frame_timestamp_ms(frame, stream, fallback_ms: float) -> int:
    if frame.time is not None:
        return int(float(frame.time) * 1000)
    if frame.pts is not None:
        return int(frame.pts * stream.time_base * 1000)
    return int(fallback_ms)


def sampled_frames(
    video_bytes: bytes, detector_fps: float
) -> Iterator[tuple[int, object]]:
    import av

    if detector_fps <= 0:
        raise ValueError("detector_fps must be positive")
    with av.open(io.BytesIO(video_bytes)) as container:
        stream = container.streams.video[0]
        interval_ms = 1000.0 / detector_fps
        next_ms = 0.0
        for frame in container.decode(stream):
            timestamp_ms = frame_timestamp_ms(frame, stream, next_ms)
            if timestamp_ms + 0.001 < next_ms:
                continue
            yield timestamp_ms, frame.to_ndarray(format="bgr24")
            next_ms = timestamp_ms + interval_ms
