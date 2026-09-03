from worker.uploads import UploadService


class Blob:
    def __init__(self, *, size=123, encrypted=True):
        self.kwargs = None
        self.generation = 42
        self.size = size
        self.customer_encryption = (
            {"encryptionAlgorithm": "AES256"} if encrypted else None
        )

    def create_resumable_upload_session(self, **kwargs):
        self.kwargs = kwargs
        return "https://storage.example.test/session"

    def reload(self, **_kwargs):
        pass


class Bucket:
    def __init__(self, blob=None):
        self.value = blob or Blob()
        self.calls = []

    def blob(self, name, **kwargs):
        self.calls.append((name, kwargs))
        return self.value


class Storage:
    def __init__(self, blob=None):
        self.bucket = Bucket(blob)
        self.csek = b"k" * 32


class Runs:
    def prepare_upload(self, run_id):
        assert run_id == "run"
        return {
            "object_name": "submissions-temporary/run/source",
            "content_type": "image/jpeg",
            "expected_bytes": 123,
        }


def test_session_is_origin_size_generation_and_csek_bound():
    storage = Storage()
    service = UploadService(storage, Runs(), "https://faces.example.test")
    assert service.create_session("run") == "https://storage.example.test/session"
    assert storage.bucket.calls == [
        ("submissions-temporary/run/source", {"encryption_key": b"k" * 32})
    ]
    assert storage.bucket.value.kwargs == {
        "content_type": "image/jpeg",
        "size": 123,
        "origin": "https://faces.example.test",
        "if_generation_match": 0,
        "timeout": 30,
    }


def test_finalize_verifies_csek_size_and_returns_generation():
    result = UploadService(
        Storage(), Runs(), "https://faces.example.test"
    ).verify_completed("run")
    assert result.generation == 42
    assert result.size == 123


def test_finalize_rejects_size_mismatch():
    service = UploadService(
        Storage(Blob(size=122)), Runs(), "https://faces.example.test"
    )
    try:
        service.verify_completed("run")
    except ValueError as error:
        assert "size differs" in str(error)
    else:
        raise AssertionError("upload size mismatch was accepted")


def test_finalize_rejects_object_without_csek_metadata():
    service = UploadService(
        Storage(Blob(encrypted=False)), Runs(), "https://faces.example.test"
    )
    try:
        service.verify_completed("run")
    except RuntimeError as error:
        assert "customer-supplied encryption" in str(error)
    else:
        raise AssertionError("unencrypted upload was accepted")
