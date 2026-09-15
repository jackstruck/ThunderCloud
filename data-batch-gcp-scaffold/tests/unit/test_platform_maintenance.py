import json

import pytest

from maintenance import local
from maintenance.cli import Report
from maintenance.migrations import MigrationError, ordered_migrations


def test_migration_manifest_rejects_missing_or_duplicate_versions(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "migrations").mkdir()
    (tmp_path / "scripts/db_schema.sql").write_text("SELECT 1;")
    (tmp_path / "migrations/002_gap.sql").write_text("SELECT 2;")
    with pytest.raises(MigrationError, match="contiguous"):
        ordered_migrations(tmp_path)


@pytest.mark.parametrize(
    "running,exit_code,report_status,expected",
    [
        (True, 0, "succeeded", "running"),
        (False, 0, None, "interrupted/incomplete"),
        (False, 0, "failed", "interrupted/incomplete"),
        (False, 137, "succeeded", "failed"),
        (False, 0, "succeeded", "succeeded"),
    ],
)
def test_status_requires_terminal_process_and_matching_report(
    tmp_path, monkeypatch, running, exit_code, report_status, expected
):
    (tmp_path / "resources.json").write_text(
        json.dumps(
            {
                "execution_id": "test",
                "runner": {"name": "test-validate", "id": "container"},
            }
        )
    )
    if report_status:
        (tmp_path / "report.json").write_text(
            json.dumps({"status": report_status, "execution_id": "test"})
        )
    monkeypatch.setattr(
        local,
        "inspect",
        lambda _: {
            "Name": "/test-validate",
            "Config": {"Labels": {local.LABEL: "test"}},
            "State": {
                "Running": running,
                "Status": "running" if running else "exited",
                "ExitCode": exit_code,
            },
        },
    )
    assert local.status(tmp_path)["status"] == expected


def test_cleanup_rejects_unowned_resource(tmp_path, monkeypatch):
    (tmp_path / "resources.json").write_text(
        json.dumps(
            {
                "execution_id": "test",
                "runner": {"name": "test-validate", "id": "container"},
            }
        )
    )
    monkeypatch.setattr(
        local,
        "inspect",
        lambda _: {
            "Name": "/test-validate",
            "Config": {"Labels": {local.LABEL: "someone-else"}},
        },
    )
    with pytest.raises(RuntimeError, match="ownership"):
        local.cleanup(tmp_path)


def test_failed_subprocess_preserves_progress_and_stage_log(tmp_path):
    import sys

    report = Report(tmp_path, "test", "validate", "sha256:" + "a" * 64)
    with pytest.raises(RuntimeError, match="Mandatory stage failed"):
        report.run(
            "synthetic-failure",
            [
                sys.executable,
                "-c",
                "print('synthetic diagnostic'); raise SystemExit(3)",
            ],
        )
    report.finish("failed", ["investigate synthetic-failure.log"])
    data = json.loads((tmp_path / "report.json").read_text())
    assert data["status"] == "failed"
    assert data["checks"]["synthetic-failure"]["exit_code"] == 3
    assert "synthetic diagnostic" in (tmp_path / "synthetic-failure.log").read_text()
    assert (tmp_path / "report.md").exists()
    with pytest.raises(RuntimeError, match="already has a report"):
        Report(tmp_path, "test", "validate", "same-image")


def test_removed_container_status_uses_verified_cleanup_export(tmp_path, monkeypatch):
    import hashlib

    (tmp_path / "resources.json").write_text(
        json.dumps(
            {
                "execution_id": "test",
                "runner": {"name": "test-validate", "id": "container"},
            }
        )
    )
    (tmp_path / "report.json").write_text(
        json.dumps({"status": "succeeded", "execution_id": "test"})
    )
    state = tmp_path / "container-state.json"
    state.write_text(json.dumps({"Status": "exited", "Running": False, "ExitCode": 0}))
    artifacts = {
        name: hashlib.sha256((tmp_path / name).read_bytes()).hexdigest()
        for name in ["report.json", "container-state.json"]
    }
    (tmp_path / "cleanup.json").write_text(
        json.dumps({"status": "incomplete", "artifacts": artifacts})
    )
    monkeypatch.setattr(local, "inspect", lambda _: None)
    assert local.status(tmp_path)["status"] == "succeeded"
    state.write_text("{}")
    assert local.status(tmp_path)["status"] == "interrupted/incomplete"


def test_docker_missing_object_is_distinct_from_daemon_failure(monkeypatch):
    from types import SimpleNamespace

    def docker(*args, **_kwargs):
        if args[0] == "info":
            return SimpleNamespace(returncode=0, stdout="daemon-id", stderr="")
        return SimpleNamespace(
            returncode=1, stdout="[]", stderr="error: no such object: test"
        )

    monkeypatch.setattr(local, "docker", docker)
    assert local.inspect("test") is None

    def disconnected(*_args, **_kwargs):
        raise RuntimeError("daemon unavailable")

    monkeypatch.setattr(local, "docker", disconnected)
    with pytest.raises(RuntimeError, match="daemon unavailable"):
        local.inspect("test")


def test_explicit_resume_preserves_prior_attempt_and_rejects_changed_image(tmp_path):
    report = Report(tmp_path, "apply-test", "apply-migration", "sha256:" + "b" * 64)
    report.finish("failed", ["retry after inspected failure"])
    with pytest.raises(RuntimeError, match="differs"):
        Report(tmp_path, "apply-test", "apply-migration", "another-image", resume=True)
    resumed = Report(
        tmp_path, "apply-test", "apply-migration", "sha256:" + "b" * 64, resume=True
    )
    assert resumed.data["attempt"] == 2
    assert not (tmp_path / "report.json").exists()
    assert (
        json.loads((tmp_path / "attempts/attempt-1.json").read_text())["status"]
        == "failed"
    )


def test_private_procedure_output_is_not_persisted(tmp_path):
    import sys

    from maintenance.cli import Report

    report = Report(tmp_path, "private-output", "rehearse-migration", "sha256:fixture")
    report.run(
        "restore",
        [
            sys.executable,
            "-c",
            "import sys; print('private-stdout'); print('private-stderr', file=sys.stderr)",
        ],
        private_output=True,
    )
    output = (tmp_path / "restore.log").read_text()
    assert "private-stdout" not in output
    assert "private-stderr" not in output
    assert "output suppressed" in output
    assert report.data["checks"]["restore"]["exit_code"] == 0
