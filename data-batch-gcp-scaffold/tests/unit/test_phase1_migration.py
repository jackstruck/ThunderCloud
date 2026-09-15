from pathlib import Path


def test_phase1_migration_contains_required_durable_contracts():
    sql = Path("migrations/001_phase1_runs.sql").read_text()
    for table in (
        "media_run",
        "submission_face_group",
        "run_candidate",
        "run_operation",
        "subject_representative_face",
        "run_cleanup_object",
        "gallery_cleanup_object",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql
    assert "handling_policy = 'search_then_discard'" in sql
    assert "aggregate_embedding vector(512) NOT NULL" in sql
    assert "UNIQUE (submitter_principal, idempotency_key)" in sql
    assert "UNIQUE (object_name, object_generation)" in sql
