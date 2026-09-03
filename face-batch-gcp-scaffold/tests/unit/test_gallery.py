from worker.gallery import GalleryService, PrivateImage


class Repository:
    def preview(self, run_id, group_id):
        return PrivateImage(f"submissions-temporary/{run_id}/{group_id}.jpg", 2)

    def representative(self, representative_id):
        return PrivateImage(f"subject-gallery/{representative_id}.jpg", 3)

    def subject(self, subject_id):
        return {"subject_id": subject_id}


class Storage:
    def __init__(self):
        self.calls = []

    def download_private_jpeg(self, name, generation):
        self.calls.append((name, generation))
        return b"jpeg"


def test_service_resolves_server_side_object_and_exact_generation():
    storage = Storage()
    service = GalleryService(Repository(), storage)
    assert service.preview("run", "group") == b"jpeg"
    assert service.representative("face") == b"jpeg"
    assert storage.calls == [
        ("submissions-temporary/run/group.jpg", 2),
        ("subject-gallery/face.jpg", 3),
    ]
