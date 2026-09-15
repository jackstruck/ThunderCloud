from google.api_core.exceptions import NotFound

from worker.maintenance import CleanupObject, maintain


class Repository:
    def __init__(self):
        self.items = [
            CleanupObject("cleanup", "submissions-temporary/run/a.jpg", 7, "owner", 1)
        ]
        self.finished = []

    def expire_runs(self):
        return 2

    def claim_cleanup(self):
        return self.items.pop(0) if self.items else None

    def unresolved_uploads(self):
        return []

    def record_orphan(self, run_id, object_name, generation):
        self.orphan = (run_id, object_name, generation)

    def finish_cleanup(self, item, error_code=None):
        self.finished.append((item, error_code))

    def claim_gallery_cleanup(self):
        return None

    def finish_gallery_cleanup(self, item, error_code=None):
        self.gallery_finished = (item, error_code)


class Storage:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def delete_temporary(self, name, generation):
        self.calls.append((name, generation))
        if self.error:
            raise self.error

    def temporary_generation(self, _name):
        return 9

    def delete_gallery_face(self, name, generation):
        self.gallery_call = (name, generation)


def test_maintenance_expires_and_deletes_exact_generation():
    repository = Repository()
    storage = Storage()
    assert maintain(repository, storage) == {
        "expired": 2,
        "deleted": 1,
        "gallery_deleted": 0,
        "failed": 0,
    }
    assert storage.calls == [("submissions-temporary/run/a.jpg", 7)]
    assert repository.finished[0][1] is None


def test_missing_object_is_idempotent_success():
    repository = Repository()
    assert maintain(repository, Storage(NotFound("gone")))["deleted"] == 1
    assert repository.finished[0][1] is None


def test_cleanup_failure_remains_retryable_without_changing_run():
    repository = Repository()
    assert maintain(repository, Storage(RuntimeError("outage")))["failed"] == 1
    assert repository.finished[0][1] == "deletion_failed"


def test_completed_but_unfinalized_upload_is_discovered_by_generation():
    repository = Repository()
    repository.unresolved_uploads = lambda: [
        ("run", "submissions-temporary/run/source.jpg")
    ]
    maintain(repository, Storage())
    assert repository.orphan == (
        "run",
        "submissions-temporary/run/source.jpg",
        9,
    )


def test_retired_gallery_object_is_deleted_after_publication_grace():
    repository = Repository()
    item = CleanupObject("gallery-cleanup", "subject-gallery/a/b.jpg", 12, "owner", 1)
    gallery_items = [item]
    repository.claim_gallery_cleanup = lambda: (
        gallery_items.pop(0) if gallery_items else None
    )
    storage = Storage()
    result = maintain(repository, storage)
    assert result["gallery_deleted"] == 1
    assert storage.gallery_call == ("subject-gallery/a/b.jpg", 12)
    assert repository.gallery_finished == (item, None)
