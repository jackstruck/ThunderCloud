import hashlib

import pytest

from maintenance.migrations import MigrationError
from maintenance.rehearsal import verify_backup


def test_backup_receipt_rejects_wrong_copy_and_unsupported_baseline(tmp_path):
    backup = tmp_path / "backup.dump"
    backup.write_bytes(b"PGDMPsynthetic")
    receipt = {
        "format": 1,
        "sha256": hashlib.sha256(backup.read_bytes()).hexdigest(),
        "bytes": backup.stat().st_size,
        "starting_version": 10,
        "source": {"database": "synthetic"},
        "captured_at": "2026-09-09T00:00:00Z",
    }
    verify_backup(backup, receipt)
    with pytest.raises(MigrationError, match="baseline"):
        verify_backup(backup, {**receipt, "starting_version": 9})
    with pytest.raises(MigrationError, match="does not match"):
        verify_backup(backup, {**receipt, "sha256": "0" * 64})
    backup.write_bytes(b"not-a-database")
    with pytest.raises(MigrationError, match="custom-format"):
        verify_backup(
            backup,
            {
                **receipt,
                "sha256": hashlib.sha256(backup.read_bytes()).hexdigest(),
                "bytes": backup.stat().st_size,
            },
        )
