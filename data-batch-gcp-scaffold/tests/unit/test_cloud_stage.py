from pathlib import Path
from types import SimpleNamespace

import pytest

from maintenance.cloud_stage import fingerprints, upload


class Bucket:
    name = "private"

    def __init__(self):
        self.objects = {}
        self.uploads = 0

    def get_blob(self, key):
        return self.objects.get(key)

    def blob(self, key):
        bucket = self

        class Blob:
            def upload_from_filename(self, path, **kwargs):
                assert kwargs["if_generation_match"] == 0
                assert key not in bucket.objects
                _, self.crc32c, self.size = fingerprints(Path(path))
                self.generation = 7
                bucket.objects[key] = self
                bucket.uploads += 1

            def reload(self):
                pass

        return Blob()


def test_interrupted_staging_reuses_verified_content_addressed_object(tmp_path):
    path = tmp_path / "backup"
    path.write_bytes(b"protected backup fixture")
    store = SimpleNamespace(prefix="migration/inputs", bucket=Bucket())
    first = upload(store, "backup", path)
    second = upload(store, "backup", path)
    assert first == second
    assert first["generation"] == 7
    assert store.bucket.uploads == 1
    store.bucket.objects[next(iter(store.bucket.objects))].crc32c = "corrupt"
    with pytest.raises(ValueError, match="differs"):
        upload(store, "backup", path)
