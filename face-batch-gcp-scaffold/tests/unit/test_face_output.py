import json

from worker.models import Candidate, TrackTemplate
from worker.process import export_face_crops


def test_export_face_crops(tmp_path):
    candidate = Candidate(0.75, 1234, [1.0, 0.0], {"sharpness": 0.8}, b"jpeg")
    template = TrackTemplate(7, 1000, 1500, 2, [candidate], [1.0, 0.0])

    result = export_face_crops(tmp_path, "job-1", "gs://bucket/videos/a.mp4", [template])

    crop = result / "track-000007" / "rank-01_time-000000001234ms.jpg"
    assert crop.read_bytes() == b"jpeg"
    manifest = json.loads((result / "manifest.json").read_text())
    assert manifest["source"] == "gs://bucket/videos/a.mp4"
    assert manifest["faces"][0]["track_id"] == 7
