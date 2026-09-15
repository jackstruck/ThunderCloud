"""Migration acceptance uses only execution-owned localhost databases."""

import hashlib
import os
import uuid
from dataclasses import replace
from pathlib import Path

import pg8000
import pytest

from maintenance.migrations import (
    LOCK,
    Migration,
    MigrationError,
    apply_migrations,
    build_reference,
    ordered_migrations,
    schema_fingerprint,
)

pytestmark = pytest.mark.skipif(
    not os.getenv("FACE_SUBJECT_TEST_PORT"), reason="isolated PostgreSQL port required"
)
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def connections():
    opened, names = [], []

    def connect(name="postgres"):
        c = pg8000.connect(
            user="postgres",
            host="127.0.0.1",
            port=int(os.environ["FACE_SUBJECT_TEST_PORT"]),
            database=name,
        )
        opened.append(c)
        return c

    admin = connect()
    admin.autocommit = True

    def new(existing=None):
        if existing is not None:
            assert existing in names
            return connect(existing)
        name = "migration_test_" + uuid.uuid4().hex
        admin.cursor().execute(f'CREATE DATABASE "{name}"')
        names.append(name)
        return connect(name)

    yield new
    for c in opened[1:]:
        c.close()
    for name in names:
        admin.cursor().execute(f'DROP DATABASE "{name}" WITH (FORCE)')
    admin.close()


def rows(c, query, args=()):
    cur = c.cursor()
    cur.execute(query, args)
    result = list(cur.fetchall()) if cur.description else []
    c.commit()
    return result


def test_fresh_upgrade_and_verified_adoption_agree(connections):
    migrations = ordered_migrations(ROOT)
    fresh = connections()
    reference = build_reference(fresh, migrations)
    upgraded, adopted = connections(), connections()
    apply_migrations(upgraded, migrations[:6])
    for m in migrations[:6]:
        adopted.cursor().execute(m.sql)
    adopted.commit()
    for c in [upgraded, adopted]:
        rows(
            c,
            """INSERT INTO subject(subject_id,model_version,canonical_embedding,sample_count)
            VALUES ('00000000-0000-0000-0000-000000000001','synthetic',%s::vector,1)""",
            ("[1," + ",".join(["0"] * 511) + "]",),
        )
    before = rows(adopted, "SELECT subject_id,canonical_embedding::text FROM subject")
    apply_migrations(upgraded, migrations, reference=reference)
    apply_migrations(adopted, migrations, reference=reference, adopt_through=5)
    assert (
        rows(upgraded, "SELECT subject_id,canonical_embedding::text FROM subject")
        == before
    )
    assert (
        rows(adopted, "SELECT subject_id,canonical_embedding::text FROM subject")
        == before
    )
    assert rows(
        adopted, "SELECT count(*) FROM platform_schema_migration WHERE adopted"
    ) == [[6]]
    for c in [fresh, upgraded, adopted]:
        assert (
            schema_fingerprint(c.cursor())
            == reference["schemas"][str(migrations[-1].version)]
        )
        c.rollback()
        apply_migrations(c, migrations, reference=reference)
        assert rows(c, "SELECT count(*) FROM platform_schema_migration") == [
            [len(migrations)]
        ]


def test_unverified_adoption_and_schema_drift_are_rejected(connections):
    migrations = ordered_migrations(ROOT)
    reference = build_reference(connections(), migrations)
    c = connections()
    for m in migrations:
        c.cursor().execute(m.sql)
    c.commit()
    with pytest.raises(MigrationError, match="explicit adoption"):
        apply_migrations(c, migrations)
    rows(c, "ALTER TABLE subject DROP COLUMN row_version")
    with pytest.raises(MigrationError, match="differs from the verified reference"):
        apply_migrations(c, migrations, reference=reference, adopt_through=10)
    assert rows(c, "SELECT to_regclass('platform_schema_migration')") == [[None]]
    tracked = connections()
    apply_migrations(tracked, migrations)
    rows(
        tracked,
        "ALTER TABLE subject_example DISABLE TRIGGER protect_subject_example_origin",
    )
    with pytest.raises(MigrationError, match="schema changed"):
        apply_migrations(tracked, migrations)


def test_membership_migrations_preserve_retained_data(connections):
    from maintenance.preservation import capture, verify

    migrations = ordered_migrations(ROOT)
    c = connections()
    apply_migrations(c, [m for m in migrations if m.version < 14])
    source, subject, job, track, example = [uuid.uuid4() for _ in range(5)]
    vector = "[1," + ",".join(["0"] * 511) + "]"
    rows(
        c,
        "INSERT INTO source_asset(source_id,external_source_ref,source_sha256) "
        "VALUES (%s,'migration-fixture',%s)",
        (source, "a" * 64),
    )
    rows(
        c,
        "INSERT INTO subject(subject_id,model_version,canonical_embedding,sample_count) "
        "VALUES (%s,'synthetic',%s::vector,1)",
        (subject, vector),
    )
    rows(
        c,
        """INSERT INTO processing_job(job_id,idempotency_key,source_id,worker_version,
         detector_version,embedding_model_version,threshold_version,status)
         VALUES (%s,'migration-fixture',%s,'synthetic','synthetic','synthetic','old','succeeded')""",
        (job, source),
    )
    rows(
        c,
        """INSERT INTO face_track(track_id,source_id,processing_job_id,subject_id,
         local_track_id,start_ms,end_ms,aggregate_embedding,model_version,
         observation_count,embedded_count,decision)
         VALUES (%s,%s,%s,%s,1,0,100,%s::vector,'synthetic',1,1,'unknown')""",
        (track, source, job, subject, vector),
    )
    rows(
        c,
        """INSERT INTO subject_example(example_id,subject_id,source_id,face_track_id,
         embedding,model_version,start_ms,end_ms)
         VALUES (%s,%s,%s,%s,%s::vector,'synthetic',0,100)""",
        (example, subject, source, track, vector),
    )
    # Both active images and retired images without an example retain old evidence.
    rows(
        c,
        """INSERT INTO subject_representative_face(subject_id,source_id,source_track_id,
         example_id,object_name,object_generation,content_type,active)
         VALUES (%s,%s,%s,%s,'gallery/active.jpg',1,'image/jpeg',true),
                (%s,%s,%s,NULL,'gallery/retired.jpg',2,'image/jpeg',false)""",
        (subject, source, track, example, subject, source, track),
    )
    run, group = uuid.uuid4(), uuid.uuid4()
    rows(
        c,
        """INSERT INTO media_run(run_id,submitter_principal,idempotency_key,
        request_fingerprint,handling_policy,source_kind,state)
        VALUES (%s,'fixture','fixture',%s,'enroll_only','upload','succeeded')""",
        (run, "b" * 64),
    )
    rows(
        c,
        """INSERT INTO submission_face_group(group_id,run_id,local_group_id,
        start_ms,end_ms,preview_object_name,preview_generation,detector_version,
        embedding_model_version,aggregate_embedding)
        VALUES (%s,%s,1,0,100,'fixture.jpg',1,'synthetic','synthetic',%s::vector)""",
        (group, run, vector),
    )
    rows(
        c,
        """INSERT INTO submission_enrollment(group_id,run_id,source_id,subject_id,
        decision,embedding_model_version) VALUES (%s,%s,%s,%s,'created','synthetic')""",
        (group, run, source, subject),
    )
    rows(
        c,
        """INSERT INTO subject_example(subject_id,source_id,submission_group_id,
        embedding,model_version,start_ms,end_ms) VALUES (%s,%s,%s,%s::vector,'synthetic',0,100)""",
        (subject, source, group, vector),
    )
    rows(c, "UPDATE subject SET sample_count=2 WHERE subject_id=%s", (subject,))
    rows(
        c,
        """UPDATE source_asset SET metadata='{"luluvid_url":"https://example.test/origin","label":"retained"}'::jsonb
        WHERE source_id=%s""",
        (source,),
    )
    aid = uuid.uuid4()
    rows(
        c,
        """INSERT INTO enrollment_assignment(assignment_id,run_id,destination,target_subject_id)
        VALUES (%s,%s,'existing',%s)""",
        (aid, run, subject),
    )
    rows(
        c,
        "INSERT INTO enrollment_assignment_member(group_id,assignment_id) VALUES (%s,%s)",
        (group, aid),
    )
    before = capture(c)
    assert not any(before["violations"].values())
    # Normalize an already-verified historical label without changing origin records.
    rows(
        c, "UPDATE face_track SET model_version='reported' WHERE track_id=%s", (track,)
    )
    rows(
        c, "ALTER TABLE subject_example DISABLE TRIGGER protect_subject_example_origin"
    )
    rows(
        c,
        "UPDATE subject_example SET model_version='reported' WHERE face_track_id=%s",
        (track,),
    )
    rows(c, "ALTER TABLE subject_example ENABLE TRIGGER protect_subject_example_origin")
    rows(
        c,
        """INSERT INTO verified_embedding_model(processing_job_id,reported_model_version,
        verified_model_version,evidence,verified_by) VALUES (%s,'reported','synthetic','{}','fixture')""",
        (job,),
    )
    before = capture(c)
    reference = build_reference(connections(), migrations)
    rows(
        c,
        "UPDATE media_run SET state='awaiting_face_selection' WHERE run_id=%s",
        (run,),
    )
    with pytest.raises(pg8000.DatabaseError, match="explicit disposition"):
        apply_migrations(c, migrations, reference=reference)
    assert rows(c, "SELECT max(version) FROM platform_schema_migration") == [[15]]
    rows(c, "UPDATE media_run SET state='succeeded' WHERE run_id=%s", (run,))
    apply_migrations(c, migrations, reference=reference)
    after = capture(c)
    verify(before, after)
    assert rows(
        c, "SELECT model_version FROM subject_example WHERE face_track_id=%s", (track,)
    ) == [["synthetic"]]
    assert rows(
        c, "SELECT model_version FROM face_track WHERE track_id=%s", (track,)
    ) == [["reported"]]
    assert after["records"]["assignment_members"]["rows"] == 1
    assert rows(
        c,
        """SELECT count(*) FROM information_schema.columns
        WHERE table_schema='public' AND table_name IN ('face_track','submission_enrollment')
        AND column_name='subject_id'""",
    ) == [[0]]
    assert rows(
        c,
        """SELECT count(*) FROM information_schema.columns
        WHERE table_schema='public' AND table_name='subject_representative_face'
        AND column_name IN ('subject_id','source_id','source_track_id')""",
    ) == [[0]]
    assert after["records"]["enrollment"]["rows"] == 1
    assert rows(
        c,
        "SELECT source_page_url,metadata FROM source_asset WHERE source_id=%s",
        (source,),
    ) == [["https://example.test/origin", {"label": "retained"}]]
    assert rows(
        c,
        "SELECT to_regclass('submission_enrollment'),to_regclass('platform_migration_record')",
    ) == [[None, None]]


def test_managed_storage_bucket_requires_explicit_migration_input(connections):
    from maintenance.preservation import capture, verify

    migrations = ordered_migrations(ROOT)
    c = connections()
    apply_migrations(c, [m for m in migrations if m.version < 20])
    rows(
        c,
        """INSERT INTO source_asset(external_source_ref,source_sha256,object_name,object_generation)
        VALUES ('submission:fixture',%s,'training-media/fixture/source.mp4',3)""",
        ("a" * 64,),
    )
    with pytest.raises(MigrationError, match="Explicit source bucket"):
        capture(c)
    before = capture(c, source_bucket="reviewed-bucket")
    with pytest.raises(pg8000.DatabaseError, match="reviewed retained-storage bucket"):
        apply_migrations(c, migrations)
    apply_migrations(c, migrations, source_bucket="reviewed-bucket")
    verify(before, capture(c))
    rows(c, "UPDATE source_asset SET object_bucket='wrong-bucket'")
    with pytest.raises(MigrationError, match="sources"):
        verify(before, capture(c))
    rows(c, "UPDATE source_asset SET object_bucket='reviewed-bucket'")
    assert rows(
        c, "SELECT object_bucket,object_name,object_generation FROM source_asset"
    ) == [["reviewed-bucket", "training-media/fixture/source.mp4", 3]]


def test_archive_storage_normalization_preserves_reference(connections):
    from maintenance.preservation import capture, verify
    from worker.run_repository import RunNotFoundError, RunRepository

    migrations = ordered_migrations(ROOT)
    c = connections()
    apply_migrations(c, [m for m in migrations if m.version < 22])
    sid = uuid.uuid4()
    rows(
        c,
        """INSERT INTO source_asset(source_id,external_source_ref,source_sha256,metadata)
        VALUES (%s,'gs://archive-bucket/videos/nested/source.mp4',%s,'{"generation":42,"label":"keep"}')""",
        (sid, "a" * 64),
    )
    before = capture(c)
    apply_migrations(c, migrations)
    verify(before, capture(c))
    assert rows(
        c,
        "SELECT storage_kind,object_bucket,object_name,object_generation,metadata FROM source_asset",
    ) == [
        ["archive", "archive-bucket", "videos/nested/source.mp4", 42, {"label": "keep"}]
    ]

    class Database:
        def connect(self):
            return pg8000.connect(
                user="postgres",
                host="127.0.0.1",
                port=int(os.environ["FACE_SUBJECT_TEST_PORT"]),
                database=rows(c, "SELECT current_database()")[0][0],
            )

    with pytest.raises(RunNotFoundError):
        RunRepository(Database()).tombstone_source(str(sid), "reviewer")
    assert rows(c, "SELECT deleted_at FROM source_asset") == [[None]]
    assert rows(c, "SELECT count(*) FROM run_cleanup_object") == [[0]]


def test_archive_generation_recovered_from_unique_successful_digest(connections):
    from maintenance.preservation import capture, verify

    migrations = ordered_migrations(ROOT)
    c = connections()
    apply_migrations(c, [m for m in migrations if m.version < 18])
    rows(
        c,
        """INSERT INTO source_asset(external_source_ref,source_sha256,metadata)
        VALUES ('gs://fixture/source',%s,'{"generation":null,"label":"keep"}')""",
        ("a" * 64,),
    )
    for generation, state, digest in [
        (42, "succeeded", "a"),
        (43, "succeeded", "a"),
        (44, "dead_letter", "a"),
        (45, "succeeded", "b"),
    ]:
        rollout = uuid.uuid4()
        rows(
            c,
            """INSERT INTO processing_rollout(rollout_id,name,image_digest,worker_version,
            detector_version,embedding_model_version,threshold_version,matching_enabled,creator_principal)
            VALUES (%s,'fixture','fixture','fixture','fixture','fixture','fixture',true,'fixture')""",
            (rollout,),
        )
        rows(
            c,
            """INSERT INTO processing_work_item(rollout_id,source_uri,source_sha256,
            source_generation,application_job_id,state,completed_at)
            VALUES (%s,'gs://fixture/source',%s,%s,%s,%s,now())""",
            (rollout, digest * 64, generation, uuid.uuid4(), state),
        )
    with pytest.raises(pg8000.DatabaseError, match="evidence is ambiguous"):
        apply_migrations(c, migrations)
    assert rows(c, "SELECT max(version) FROM platform_schema_migration") == [[17]]
    rows(c, "DELETE FROM processing_work_item WHERE source_generation=43")
    before = capture(c)
    apply_migrations(c, migrations)
    verify(before, capture(c))
    assert rows(
        c, "SELECT storage_kind,object_generation,metadata FROM source_asset"
    ) == [["archive", 42, {"label": "keep"}]]


def test_archive_queue_retirement_requires_drained_work(connections):
    migrations = ordered_migrations(ROOT)
    c = connections()
    apply_migrations(c, [m for m in migrations if m.version < 18])
    rollout, item = uuid.uuid4(), uuid.uuid4()
    rows(
        c,
        """INSERT INTO processing_rollout(rollout_id,name,image_digest,worker_version,
        detector_version,embedding_model_version,threshold_version,matching_enabled,creator_principal)
        VALUES (%s,'fixture','fixture','fixture','fixture','fixture','fixture',true,'fixture')""",
        (rollout,),
    )
    rows(
        c,
        """INSERT INTO processing_work_item(work_item_id,rollout_id,source_uri,
        source_sha256,application_job_id,state) VALUES (%s,%s,'gs://fixture/source',%s,%s,'retry')""",
        (item, rollout, "a" * 64, uuid.uuid4()),
    )
    with pytest.raises(pg8000.DatabaseError, match="must be drained"):
        apply_migrations(c, migrations)
    assert rows(
        c, "SELECT state FROM processing_work_item WHERE work_item_id=%s", (item,)
    ) == [["retry"]]
    assert rows(c, "SELECT max(version) FROM platform_schema_migration") == [[17]]
    rows(
        c,
        "UPDATE processing_work_item SET last_error_code='OPERATOR_CANCELLED' WHERE work_item_id=%s",
        (item,),
    )
    with pytest.raises(pg8000.DatabaseError, match="must be drained"):
        apply_migrations(c, migrations)
    rows(
        c,
        "UPDATE processing_rollout SET status='cancelled' WHERE rollout_id=%s",
        (rollout,),
    )
    apply_migrations(c, migrations)
    assert rows(
        c,
        "SELECT to_regclass('processing_work_item'),to_regclass('processing_rollout')",
    ) == [[None, None]]
    apply_migrations(c, migrations)


def test_checksum_failure_rollback_resume_and_newer_database(connections):
    migrations = ordered_migrations(ROOT)
    c = connections()
    apply_migrations(c, migrations)
    altered = [replace(migrations[0], checksum="0" * 64), *migrations[1:]]
    with pytest.raises(MigrationError, match="checksum changed"):
        apply_migrations(c, altered)
    with pytest.raises(MigrationError, match="newer"):
        apply_migrations(c, migrations[:-1])
    sql = "CREATE TABLE migration_probe(id integer); SELECT 1/0;"
    broken = Migration(
        len(migrations),
        f"{len(migrations):03d}_probe.sql",
        sql,
        hashlib.sha256(sql.encode()).hexdigest(),
    )
    with pytest.raises(pg8000.DatabaseError):
        apply_migrations(c, [*migrations, broken])
    assert rows(c, "SELECT to_regclass('migration_probe')") == [[None]]
    assert rows(c, "SELECT max(version) FROM platform_schema_migration") == [
        [migrations[-1].version]
    ]
    sql = "CREATE TABLE migration_probe(id integer);"
    fixed = replace(broken, sql=sql, checksum=hashlib.sha256(sql.encode()).hexdigest())
    apply_migrations(c, [*migrations, fixed])
    assert rows(c, "SELECT max(version) FROM platform_schema_migration") == [
        [len(migrations)]
    ]


def test_committed_checkpoint_survives_interruption(connections):
    migrations = ordered_migrations(ROOT)
    c = connections()

    def stop(m, _fingerprint):
        if m.version == 4:
            raise InterruptedError("simulated lost execution after commit")

    with pytest.raises(InterruptedError):
        apply_migrations(c, migrations, checkpoint=stop)
    assert rows(c, "SELECT max(version) FROM platform_schema_migration") == [[4]]
    apply_migrations(c, migrations)
    assert rows(c, "SELECT count(*) FROM platform_schema_migration") == [
        [len(migrations)]
    ]


def test_migration_lock_and_runtime_grants(connections):
    c = connections()
    owner = connections(rows(c, "SELECT current_database()")[0][0])
    rows(owner, "SELECT pg_advisory_lock(%s,%s)", LOCK)
    with pytest.raises(MigrationError, match="holds the lock"):
        apply_migrations(c, ordered_migrations(ROOT))
    rows(owner, "SELECT pg_advisory_unlock(%s,%s)", LOCK)
    apply_migrations(c, ordered_migrations(ROOT))
    role = "runtime_" + uuid.uuid4().hex
    rows(c, f'CREATE ROLE "{role}"')
    try:
        c.cursor().execute(
            "SELECT set_config('thundercloud.app_user',%s,true)", (role,)
        )
        c.cursor().execute((ROOT / "scripts/db_grants.sql").read_text())
        c.commit()
        assert rows(
            c,
            "SELECT has_table_privilege(%s,'subject_example','UPDATE'), "
            "has_table_privilege(%s,'verified_embedding_model','UPDATE'), "
            "has_table_privilege(%s,'platform_schema_migration','INSERT')",
            (role, role, role),
        ) == [[True, False, False]]
    finally:
        rows(c, f'DROP OWNED BY "{role}"')
        rows(c, f'DROP ROLE "{role}"')


def test_verified_adoption_compares_named_columns_not_append_order(connections):
    sql = "CREATE TABLE ordering_probe (a text, b text)"
    migration = Migration(
        0, "db_schema.sql", sql, hashlib.sha256(sql.encode()).hexdigest()
    )
    reference = build_reference(connections(), [migration])
    deployed = connections()
    rows(deployed, "CREATE TABLE ordering_probe (b text)")
    rows(deployed, "ALTER TABLE ordering_probe ADD COLUMN a text")
    result = apply_migrations(
        deployed, [migration], reference=reference, adopt_through=0
    )
    assert result["schema_fingerprint"] == reference["schemas"]["0"]


def test_reviewed_apply_gates_commit_and_resume(connections, tmp_path):
    """Exercise the apply entry point, including a disconnect after committed DDL."""
    import json
    import shutil
    from datetime import UTC, datetime, timedelta

    from maintenance.apply import apply_reviewed
    from maintenance.cli import Report
    from maintenance.migrations import digest, manifest
    from maintenance.preservation import capture
    from maintenance.rehearsal import file_checksum

    root = tmp_path / "source"
    shutil.copytree(ROOT / "scripts", root / "scripts")
    shutil.copytree(ROOT / "migrations", root / "migrations")
    next_version = len(ordered_migrations(root))
    (root / "migrations" / f"{next_version:03d}_apply_probe.sql").write_text(
        "ALTER TABLE subject ADD COLUMN apply_probe text;"
    )
    (root / "build-manifest.json").write_text(
        json.dumps({"source_checksum": "fixture"})
    )
    migrations = ordered_migrations(root)
    reference = build_reference(connections(), migrations)
    c = connections()
    for migration in migrations[:-1]:
        c.cursor().execute(migration.sql)
    c.commit()
    before = capture(c)
    database, oid = rows(
        c,
        "SELECT current_database(),oid FROM pg_database WHERE datname=current_database()",
    )[0]
    role = "apply_" + uuid.uuid4().hex
    rows(c, f'CREATE ROLE "{role}" NOLOGIN')
    # Backup bytes are a synthetic guard fixture. Actual pg_restore is covered
    # separately by the protected-copy container rehearsal.
    backup = tmp_path / "backup.dump"
    backup.write_bytes(b"PGDMPsynthetic-test-artifact")
    backup_receipt = {
        "format": 1,
        "bytes": backup.stat().st_size,
        "sha256": file_checksum(backup),
        "starting_version": next_version - 1,
        "source": {"database": database},
        "captured_at": datetime.now(UTC).isoformat(),
    }
    rehearsal = {
        "status": "succeeded",
        "image_digest": "sha256:fixture",
        "source_checksum": "fixture",
        "migration_checksum": digest(manifest(migrations)),
        "reference_checksum": digest(reference),
        "backup_sha256": file_checksum(backup),
        "before_checksum": digest(before),
        "after_checksum": digest(before),
    }
    plan = {
        "format": 1,
        "valid_until": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        "target": {"kind": "local", "database": database, "database_oid": oid},
        "application_roles": [role],
        "starting_version": next_version - 1,
        **{
            key: rehearsal[key]
            for key in [
                "image_digest",
                "source_checksum",
                "migration_checksum",
                "reference_checksum",
                "backup_sha256",
                "before_checksum",
            ]
        },
        "backup_receipt_checksum": digest(backup_receipt),
        "rehearsal_checksum": digest(rehearsal),
        "grants_checksum": file_checksum(root / "scripts/db_grants.sql"),
    }

    def run(report, reviewed=plan, checksum=None):
        apply_reviewed(
            report,
            c,
            root,
            reviewed,
            checksum or digest(reviewed),
            backup,
            backup_receipt,
            rehearsal,
            reference,
            before,
        )

    def report(name):
        return Report(tmp_path / name, name, "apply-migration", "sha256:fixture")

    try:
        with pytest.raises(MigrationError, match="plan checksum"):
            run(report("bad-checksum"), checksum="wrong")
        with pytest.raises(MigrationError, match="reviewed manifest"):
            run(report("bad-image"), {**plan, "image_digest": "changed"})
        with pytest.raises(MigrationError, match="identity differs"):
            run(
                report("bad-target"),
                {**plan, "target": {**plan["target"], "database_oid": oid + 1}},
            )
        rows(c, f'ALTER ROLE "{role}" LOGIN')
        with pytest.raises(MigrationError, match="NOLOGIN"):
            run(report("not-paused"))
        rows(c, f'ALTER ROLE "{role}" NOLOGIN')
        other = pg8000.connect(
            user="postgres",
            host="127.0.0.1",
            port=int(os.environ["FACE_SUBJECT_TEST_PORT"]),
            database=database,
        )
        with pytest.raises(MigrationError, match="Other database sessions"):
            run(report("not-drained"))
        other.close()
        assert rows(c, "SELECT to_regclass('platform_schema_migration')") == [[None]]

        interrupted = report("resume")
        update = interrupted.update

        def disconnect(**values):
            update(**values)
            if (
                values.get("last_committed_checkpoint", {}).get("version")
                == next_version
            ):
                raise InterruptedError("simulated executor disconnect after commit")

        interrupted.update = disconnect
        with pytest.raises(InterruptedError):
            run(interrupted)
        assert rows(c, "SELECT max(version) FROM platform_schema_migration") == [
            [next_version]
        ]
        resumed = Report(
            tmp_path / "resume",
            "resume",
            "apply-migration",
            "sha256:fixture",
            resume=True,
        )
        run(resumed)
        assert resumed.data["stage"] == "apply-verified"
        assert resumed.data["attempt"] == 2
        assert capture(c) == before
        assert rows(
            c,
            "SELECT has_table_privilege(%s,'subject_example','UPDATE'), has_table_privilege(%s,'platform_schema_migration','INSERT')",
            (role, role),
        ) == [[True, False]]
        assert rows(c, "SELECT count(*) FROM platform_schema_migration") == [
            [len(migrations)]
        ]
    finally:
        rows(c, f'DROP OWNED BY "{role}"')
        rows(c, f'DROP ROLE "{role}"')
