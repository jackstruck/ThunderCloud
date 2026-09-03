from worker.ingest_drain import drain
from worker.ingest_repository import FetchWork
from worker.secure_fetch import FetchResult
from worker.source_adapters import ResolvedSource


class Repository:
    def __init__(self):
        self.work = [
            FetchWork(
                "operation",
                "00000000-0000-4000-8000-000000000000",
                "https://example.test/a.jpg",
                1,
                "lease",
            )
        ]
        self.completed = []
        self.failed = []

    def claim_fetch(self):
        return self.work.pop(0) if self.work else None

    def complete_fetch(self, work, **values):
        self.completed.append((work, values))
        return True

    def fail_fetch(self, work, code, retryable):
        self.failed.append((work, code, retryable))


class Storage:
    def upload_temporary(self, run_id, data, content_type, digest):
        assert data == b"\xff\xd8\xffimage"
        return f"submissions-temporary/{run_id}/source.jpg", 3, len(data)


def test_drain_fetches_uploads_and_advances_work():
    repository = Repository()

    def fetcher(url, *, max_bytes):
        assert max_bytes == 100
        return FetchResult(url, "image/jpeg", b"\xff\xd8\xffimage", "a" * 64)

    resolver = lambda url: ResolvedSource(url, "direct")
    assert (
        drain(
            repository,
            Storage(),
            max_bytes=100,
            fetcher=fetcher,
            resolver=resolver,
        )
        == 1
    )
    assert not repository.failed
    assert repository.completed[0][1]["generation"] == 3


def test_policy_rejection_is_terminal_and_sanitized():
    repository = Repository()

    def fetcher(*_args, **_kwargs):
        raise ValueError("internal detail")

    assert (
        drain(
            repository,
            Storage(),
            max_bytes=100,
            fetcher=fetcher,
            resolver=lambda url: ResolvedSource(url, "direct"),
        )
        == 0
    )
    assert repository.failed[0][1:] == ("source_rejected", False)


def test_transient_fetch_error_is_retryable():
    repository = Repository()

    def fetcher(*_args, **_kwargs):
        raise RuntimeError("connection failed")

    drain(
        repository,
        Storage(),
        max_bytes=100,
        fetcher=fetcher,
        resolver=lambda url: ResolvedSource(url, "direct"),
    )
    assert repository.failed[0][1:] == ("fetch_failed", True)
