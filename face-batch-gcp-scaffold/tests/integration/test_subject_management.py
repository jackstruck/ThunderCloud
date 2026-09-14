"""Real PostgreSQL checks. Set FACE_SUBJECT_TEST_PORT to an isolated localhost DB."""

import json
import math
import os
import uuid
from pathlib import Path
from types import SimpleNamespace

import pg8000
import pytest

from maintenance.migrations import apply_migrations, ordered_migrations
from worker.db import Database, pgvector
from worker.interactive import InteractiveProcessor
from worker.interactive_repository import InteractiveRepository
from worker.run_repository import RunRepository
from worker.subject_management import SubjectError, SubjectManagement

pytestmark = pytest.mark.skipif(
    not os.getenv("FACE_SUBJECT_TEST_PORT"), reason="isolated PostgreSQL port required"
)


class LocalDatabase(Database):
    def __init__(self):
        pass

    def connect(self):
        return pg8000.connect(
            user="postgres",
            host="127.0.0.1",
            port=int(os.environ["FACE_SUBJECT_TEST_PORT"]),
            database="postgres",
        )


@pytest.fixture(scope="module")
def database():
    db = LocalDatabase()
    c = db.connect()
    cur = c.cursor()
    cur.execute("SELECT to_regclass('media_run')")
    if cur.fetchone()[0]:
        cur.execute(
            "TRUNCATE identity,subject,source_asset,media_run,subject_change_event,gallery_cleanup_object CASCADE"
        )
    c.commit()
    apply_migrations(c, ordered_migrations(Path(".")), source_bucket="test-bucket")
    c.close()
    return db


@pytest.fixture
def db(database):
    c = database.connect()
    c.cursor().execute(
        "TRUNCATE identity,subject,source_asset,media_run,subject_change_event,gallery_cleanup_object CASCADE"
    )
    c.commit()
    c.close()
    return database


def sql(db, statement, args=()):
    c = db.connect()
    try:
        cur = c.cursor()
        cur.execute(statement, args)
        rows = cur.fetchall() if cur.description else []
        c.commit()
        return rows
    finally:
        c.close()


def vector(angle=0):
    return [math.cos(angle), math.sin(angle)] + [0.0] * 510


def make_run(db, count=1, policy="enroll_only", angle=0):
    run = str(uuid.uuid4())
    digest = uuid.uuid4().hex * 2
    sql(
        db,
        """INSERT INTO media_run(run_id,submitter_principal,idempotency_key,request_fingerprint,
        handling_policy,source_kind,state,source_sha256,object_name,object_generation,object_bytes,content_type)
        VALUES (%s,'test-user',%s,%s,%s,'upload','awaiting_face_selection',%s,%s,1,100,'video/mp4')""",
        (run, run, digest, policy, digest, f"submissions-temporary/{run}/source.mp4"),
    )
    groups = []
    for i in range(count):
        gid = str(uuid.uuid4())
        groups.append(gid)
        sql(
            db,
            """INSERT INTO submission_face_group(group_id,run_id,local_group_id,start_ms,end_ms,
            quality_summary,preview_object_name,preview_generation,representative_object_name,representative_generation,
            detector_version,embedding_model_version,aggregate_embedding)
            VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,1,%s,1,'detector','model',%s::vector)""",
            (
                gid,
                run,
                i,
                i * 1000,
                (i + 1) * 1000,
                json.dumps({"max_quality": 0.8}),
                f"submissions-temporary/{run}/{gid}-sheet.jpg",
                f"submissions-temporary/{run}/{gid}.jpg",
                pgvector(vector(angle)),
            ),
        )
    return run, groups


class Storage:
    bucket_name = "test-bucket"

    def __init__(self):
        self.uploads, self.promotions = [], []

    def download_private_jpeg(self, *_args):
        return b"\xff\xd8\xfftest"

    def upload_gallery_face(self, sid, rid, data):
        self.uploads.append((sid, rid))
        return f"subject-gallery/{sid}/{rid}.jpg", 1

    def promote_temporary(self, sid, *_args):
        self.promotions.append(sid)
        return f"training-media/{sid}/source.mp4", 1, 100


def processor(db):
    storage = Storage()
    settings = SimpleNamespace(
        detector_model=None,
        embedding_model=None,
        embedding_color_order="BGR",
        require_cuda=False,
        embedding_model_version="model",
        top_k=10,
    )
    return InteractiveProcessor(
        settings, InteractiveRepository(db), storage, object(), object()
    ), storage


def assignment(groups):
    return {
        "assignment_id": str(uuid.uuid4()),
        "group_ids": groups,
    }


def enroll(db, count=1, angle=0, policy="enroll_only"):
    run, groups = make_run(db, count, policy, angle)
    assignments = [assignment(groups)]
    repo = RunRepository(db)
    assert repo.select(run, groups, 1, assignments)["state"] == "matching"
    worker, storage = processor(db)
    assert worker.match(run)
    sid = str(
        sql(
            db,
            "SELECT DISTINCT subject_id FROM subject_example JOIN submission_face_group ON submission_group_id=group_id WHERE run_id=%s",
            (run,),
        )[0][0]
    )
    return sid, run, groups, assignments, storage


@pytest.mark.parametrize("policy", ["enroll_only", "retain_and_enroll"])
def test_group_28_and_replay(db, policy):
    sid, run, groups, assignments, storage = enroll(db, 28, policy=policy)
    management = SubjectManagement(db)
    detail = management.subject(sid)
    assert detail["example_count"] == detail["sample_count"] == 28
    assert detail["source_count"] == 1
    assert len(detail["representative_faces"]) == 5
    assert len(storage.uploads) == 28
    assert bool(storage.promotions) == (policy == "retain_and_enroll")
    assert (
        sql(
            db,
            "SELECT count(DISTINCT subject_id) FROM subject_example JOIN submission_face_group ON submission_group_id=group_id WHERE run_id=%s",
            (run,),
        )[0][0]
        == 1
    )
    assert RunRepository(db).select(run, groups, 1, assignments)["state"] == "succeeded"
    worker, storage2 = processor(db)
    assert not worker.match(run)
    assert not storage2.uploads
    assert management.coverage()["count_mismatches"] == 0
    assert management.sources(sid)["sources"][0]["example_count"] == 28


def test_existing_assignment_is_rejected_atomically(db):
    existing, *_ = enroll(db)
    run, groups = make_run(db, 2)
    with pytest.raises(SubjectError, match="only an assignment ID and member IDs"):
        RunRepository(db).select(
            run,
            groups,
            1,
            [
                {
                    **assignment(groups),
                    "destination": "existing",
                    "target_subject_id": existing,
                }
            ],
        )
    assert (
        sql(db, "SELECT state FROM media_run WHERE run_id=%s", (run,))[0][0]
        == "awaiting_face_selection"
    )
    assert (
        sql(db, "SELECT count(*) FROM enrollment_assignment WHERE run_id=%s", (run,))[
            0
        ][0]
        == 0
    )
    assert SubjectManagement(db).subject(existing)["example_count"] == 1


def test_search_only_and_legacy_selection(db):
    run, groups = make_run(db, policy="search_then_discard")
    with pytest.raises(SubjectError, match="enrollment policy"):
        RunRepository(db).select(run, groups, 1, [assignment(groups)])
    assert (
        sql(db, "SELECT state FROM media_run WHERE run_id=%s", (run,))[0][0]
        == "awaiting_face_selection"
    )
    RunRepository(db).select(run, groups, 1)
    worker, storage = processor(db)
    assert worker.match(run)
    assert not storage.uploads and not storage.promotions
    assert sql(db, "SELECT count(*) FROM subject_example")[0][0] == 0
    run2, groups2 = make_run(db)
    RunRepository(db).select(run2, groups2, 1)
    assert worker.match(run2)
    assert sql(db, "SELECT count(*) FROM subject_example")[0][0] == 1


def test_assignment_membership_is_atomic(db):
    run, groups = make_run(db, 2)
    with pytest.raises(SubjectError, match="one enrollment group"):
        RunRepository(db).select(
            run, groups, 1, [assignment(groups), assignment(groups[:1])]
        )
    assert sql(db, "SELECT count(*) FROM enrollment_assignment")[0][0] == 0
    assert (
        sql(db, "SELECT state FROM media_run WHERE run_id=%s", (run,))[0][0]
        == "awaiting_face_selection"
    )


def test_edit_shared_identity_unique_ref_and_operation_replay(db):
    sid, *_ = enroll(db)
    service = SubjectManagement(db)
    before = service.subject(sid)
    data = {
        "operation_id": str(uuid.uuid4()),
        "version": before["version"],
        "identity_version": None,
        "display_name": "Example",
        "external_identity_ref": "ref-1",
    }
    result = service.edit(sid, data, "tester")
    assert service.edit(sid, data, "tester")["display_name"] == "Example"
    other, *_ = enroll(db, angle=1)
    sql(
        db,
        "UPDATE subject SET identity_id=%s WHERE subject_id=%s",
        (result["identity_id"], other),
    )
    result = service.subject(sid)
    assert set(result["shared_identity_subject_ids"]) == {sid, other}
    edited = service.edit(
        sid,
        {
            **data,
            "operation_id": str(uuid.uuid4()),
            "version": result["version"],
            "identity_version": result["identity_version"],
            "display_name": "Changed",
        },
        "tester",
    )
    assert service.subject(other)["display_name"] == "Changed"
    with pytest.raises(SubjectError, match="changed"):
        service.edit(sid, {**data, "operation_id": str(uuid.uuid4())}, "tester")
    third, *_ = enroll(db, angle=2)
    with pytest.raises(SubjectError, match="already in use"):
        service.edit(
            third,
            {
                **data,
                "operation_id": str(uuid.uuid4()),
                "version": service.subject(third)["version"],
            },
            "tester",
        )
    assert service.subject(third)["identity_id"] is None
    assert edited["identity_version"] == 2


def test_move_recalculates_raw_examples_and_gallery_provenance(db):
    sid, *_ = enroll(db, 2)
    target, *_ = enroll(db, angle=1.2)
    service = SubjectManagement(db)
    before = service.subject(sid)
    other = service.subject(target)
    example = service.examples(sid)["examples"][0]
    data = {
        "operation_id": str(uuid.uuid4()),
        "version": before["version"],
        "target_subject_id": target,
        "target_version": other["version"],
        "example_ids": [example["example_id"]],
    }
    result = service.move(sid, data, "tester")
    assert (
        result["subject"]["example_count"] == 1
        and result["destination"]["example_count"] == 2
    )
    assert service.move(sid, data, "tester")["moved_example_ids"] == [
        example["example_id"]
    ]
    record = sql(
        db,
        "SELECT e.subject_id,r.object_name,e.source_id FROM subject_representative_face r JOIN subject_example e USING(example_id) WHERE example_id=%s",
        (example["example_id"],),
    )[0]
    assert (
        str(record[0]) == target
        and f"/{sid}/" in record[1]
        and str(record[2]) == example["source_id"]
    )
    canonical = json.loads(
        sql(
            db,
            "SELECT canonical_embedding::text FROM subject WHERE subject_id=%s",
            (target,),
        )[0][0]
    )
    assert canonical[0] == pytest.approx(math.cos(0.6), abs=1e-6)
    assert canonical[1] == pytest.approx(math.sin(0.6), abs=1e-6)
    with pytest.raises(Exception, match="immutable"):
        sql(
            db,
            "UPDATE subject_example SET embedding=%s::vector WHERE example_id=%s",
            (pgvector(vector(0.2)), example["example_id"]),
        )
    last = service.examples(sid)["examples"][0]["example_id"]
    result = service.move(
        sid,
        {
            "operation_id": str(uuid.uuid4()),
            "version": service.subject(sid)["version"],
            "target_subject_id": None,
            "target_version": None,
            "example_ids": [last],
        },
        "tester",
    )
    assert result["subject"]["sample_count"] == 0
    assert result["destination"]["sample_count"] == 1


def test_retained_gallery_backfill_follows_moved_example_and_retries(db):
    from worker.gallery_backfill import RegeneratedTrack
    from worker.subject_backfill import SubjectGalleryRepository

    sid, _, groups, *_ = enroll(db, policy="retain_and_enroll")
    sql(
        db,
        "DELETE FROM subject_representative_face r USING subject_example e WHERE r.example_id=e.example_id AND e.subject_id=%s",
        (sid,),
    )
    repository = SubjectGalleryRepository(db, "test-bucket")
    candidates = repository.candidates(5, "model")
    assert len(candidates) == 1
    existing = candidates[0]
    assert existing.track_id == groups[0]
    assert existing.source_uri.startswith("gs://test-bucket/training-media/")
    assert repository.candidates(5, "other-model") == []
    service = SubjectManagement(db)
    moved = service.move(
        sid,
        {
            "operation_id": str(uuid.uuid4()),
            "version": service.subject(sid)["version"],
            "example_ids": groups,
            "target_subject_id": None,
            "target_version": None,
        },
        "test-user",
    )
    destination = moved["destination"]["subject_id"]
    rid = str(uuid.uuid4())
    name = f"subject-gallery/{sid}/{rid}.jpg"
    crop = RegeneratedTrack(0, 10, 10, vector(), 0.9, b"jpeg")
    for _ in range(2):
        repository.stage(existing, crop, rid, name, 1)
        repository.publish(sid, [rid])
    rows = sql(
        db,
        "SELECT e.subject_id,r.example_id,r.object_name,r.active FROM subject_representative_face r JOIN subject_example e USING(example_id)",
    )
    assert [(str(r[0]), str(r[1]), r[2], r[3]) for r in rows] == [
        (destination, groups[0], name, True)
    ]
    assert (
        sql(
            db,
            "SELECT count(*) FROM gallery_cleanup_object WHERE object_name=%s",
            (name,),
        )[0][0]
        == 0
    )
    replacement_name = f"subject-gallery/{destination}/{rid}.jpg"
    repository.stage(existing, crop, rid, replacement_name, 2)
    assert sql(
        db,
        "SELECT object_name,object_generation FROM subject_representative_face WHERE representative_id=%s",
        (rid,),
    )[0] == [name, 1]
    assert (
        sql(
            db,
            "SELECT count(*) FROM gallery_cleanup_object WHERE object_name=%s",
            (replacement_name,),
        )[0][0]
        == 1
    )
    assert repository.candidates(5, "model") == []
    assert service.coverage()["subjects_with_gallery"] == 1


@pytest.fixture
def browser_console(db):
    if not os.getenv("FACE_BROWSER_TESTS"):
        pytest.skip("Set FACE_BROWSER_TESTS=1 for Chromium flows")
    import threading

    import cv2
    import numpy as np
    from playwright.sync_api import sync_playwright
    from werkzeug.serving import make_server

    from worker.console import create_app
    from worker.gallery import GalleryRepository, GalleryService

    class ImageStorage:
        def download_private_jpeg(self, *_):
            return cv2.imencode(".jpg", np.zeros((20, 20, 3), dtype=np.uint8))[
                1
            ].tobytes()

    class Limiter:
        def allow(self, *_):
            return True

    worker, _ = processor(db)
    origin = "http://127.0.0.1:58123"
    app = create_app(
        RunRepository(db),
        allowed_origin=origin,
        enrollment_enabled=True,
        subject_management_enabled=True,
        subject_service=SubjectManagement(db),
        gallery_service=GalleryService(GalleryRepository(db), ImageStorage()),
        rate_limiter=Limiter(),
        invoke_gpu_match=worker.match,
    )
    server = make_server("127.0.0.1", 58123, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(
                extra_http_headers={
                    "X-Goog-Authenticated-User-Email": "accounts.google.com:test@example.test"
                }
            )
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            yield page, origin
            assert errors == []
            browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_browser_edit_search_move_and_combine(db, browser_console):
    from playwright.sync_api import expect

    page, origin = browser_console
    sid, *_ = enroll(db, 2)
    target, *_ = enroll(db, angle=1.0)
    page.goto(origin + "/subjects")
    page.get_by_label("Subject ID or display name").fill(sid)
    page.get_by_role("button", name="Search Subjects", exact=True).click()
    expect(page.locator(".subject-card")).to_have_count(1)
    page.get_by_role("link", name="Open subject").click()
    expect(page.get_by_label("Display name", exact=True)).to_be_hidden()
    expect(page.get_by_role('button', name='Save details', exact=True)).to_be_hidden()
    page.get_by_role('button', name='Edit identity', exact=True).click()
    expect(page.get_by_label("Display name", exact=True)).to_be_focused()
    page.get_by_label("Display name", exact=True).fill("<img src=x onerror=alert(1)>")
    page.get_by_label("External identity reference").fill("browser-reference")
    page.get_by_role("button", name="Save details").click()
    expect(page.locator("#subject-detail h1")).to_have_text(
        "<img src=x onerror=alert(1)>"
    )
    expect(page.get_by_label("Display name", exact=True)).to_be_hidden()
    expect(page.locator(".example-source .button-link")).to_have_text("Open Source")
    expect(page.locator(".example-row").get_by_text("model", exact=True)).to_have_count(0)
    page.go_back()
    expect(page.get_by_label("Subject ID or display name")).to_have_value(sid)
    page.go_forward()
    page.reload()
    expect(page.get_by_label("External identity reference")).to_have_value(
        "browser-reference"
    )
    page.locator(".example-row input").first.check()
    page.get_by_role("button", name="Move 1 selected examples").click()
    page.get_by_role("button", name="Create a new subject", exact=True).click()
    page.get_by_role("dialog").get_by_role(
        "button", name="Move examples", exact=True
    ).click()
    expect(page).not_to_have_url(origin + "/subjects/" + sid)
    expect(page.locator(".example-row")).to_have_count(1)
    moved = page.url.split("/")[-1]
    page.get_by_role("button", name="Merge subjects").click()
    page.locator(".correction-panel").get_by_label("Subject ID or display name").fill(
        target
    )
    page.locator(".correction-panel").get_by_role(
        "button", name="Search Subjects", exact=True
    ).click()
    page.locator(".correction-panel").get_by_label("Select for merge").check()
    page.locator(".correction-panel").get_by_role("button", name="Review selected merge").click()
    page.get_by_role("dialog").get_by_label(
        "Keep this subject and its identity details"
    ).select_option(target)
    page.get_by_role("dialog").get_by_role(
        "button", name="Merge entire subjects", exact=True
    ).click()
    expect(page).to_have_url(origin + "/subjects/" + target)
    expect(page.locator(".example-row")).to_have_count(2)
    page.goto(origin + "/subjects/" + moved)
    expect(page.locator("#subject-detail")).to_contain_text(
        "was combined into " + target
    )
    assert SubjectManagement(db).coverage()["count_mismatches"] == 0


def test_browser_grouping_edit_members_and_new_subject(db, browser_console):
    from playwright.sync_api import expect

    page, origin = browser_console
    sid, *_ = enroll(db, angle=1.0)
    run, _groups = make_run(db, 28)
    page.goto(origin + "/runs/" + run)
    page.get_by_role("button", name="Select all", exact=True).click()
    page.get_by_role("button", name="Group selected as one person").click()
    page.get_by_role("button", name="Create one new subject", exact=True).click()
    expect(page.locator(".assignment-row")).to_contain_text("28 tracks")
    first_id = page.evaluate("state.assignments[0].assignment_id")
    page.get_by_role("button", name="Edit group").click()
    page.locator(".group-members input").last.uncheck()
    page.get_by_role("button", name="Create one new subject", exact=True).click()
    expect(page.locator(".assignment-row")).to_contain_text("27 tracks")
    assert page.evaluate("state.assignments[0].assignment_id") == first_id
    assert page.locator("#faces input:checked").count() == 28
    page.get_by_role("button", name="Edit group").click()
    page.locator(".group-members input").last.check()
    page.get_by_role("button", name="Create one new subject", exact=True).click()
    page.get_by_role("button", name="Review and enroll selected faces").click()
    expect(page.get_by_role("dialog")).to_contain_text("28 tracks")
    expect(page.get_by_role("dialog")).to_contain_text("28 tracks → 1 new subject")
    page.get_by_role("dialog").get_by_role("button", name="Start enrollment").click()
    expect(page.locator("#results")).to_have_class("view active", timeout=20000)
    assert SubjectManagement(db).subject(sid)["example_count"] == 1
    enrolled = {
        str(r[0])
        for r in sql(
            db,
            "SELECT subject_id FROM subject_example JOIN submission_face_group ON submission_group_id=group_id WHERE run_id=%s",
            (run,),
        )
    }
    assert len(enrolled) == 1 and sid not in enrolled
    assert SubjectManagement(db).subject(next(iter(enrolled)))["example_count"] == 28


def test_subject_api_auth_origin_conflicts_and_feature_gate(db):
    from concurrent.futures import ThreadPoolExecutor

    from worker.console import create_app
    from worker.gallery import GalleryRepository, GalleryService

    sid, *_ = enroll(db)
    service = SubjectManagement(db)
    origin = "https://test.example"
    headers = {
        "Origin": origin,
        "X-Goog-Authenticated-User-Email": "accounts.google.com:tester@example.test",
    }
    app = create_app(
        RunRepository(db),
        allowed_origin=origin,
        enrollment_enabled=True,
        subject_management_enabled=True,
        subject_service=service,
        gallery_service=GalleryService(GalleryRepository(db), Storage()),
    )
    client = app.test_client()
    assert client.get("/api/subjects").status_code == 401
    assert client.get("/subjects/" + sid, headers=headers).status_code == 200
    assert (
        client.patch(
            "/api/subjects/" + sid,
            json={},
            headers={**headers, "Origin": "https://elsewhere.example"},
        ).status_code
        == 403
    )
    assert client.get("/api/subjects?limit=bad", headers=headers).status_code == 422
    assert (
        client.get("/api/subjects?q=" + str(uuid.uuid4()), headers=headers).status_code
        == 404
    )
    assert client.get("/api/subjects/malformed", headers=headers).status_code == 422
    version = service.subject(sid)["version"]

    def edit(name):
        return (
            app.test_client()
            .patch(
                "/api/subjects/" + sid,
                headers=headers,
                json={
                    "operation_id": str(uuid.uuid4()),
                    "version": version,
                    "identity_version": None,
                    "display_name": name,
                    "external_identity_ref": None,
                },
            )
            .status_code
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(edit, ["First", "Second"])) == [200, 409]
    assert [row[0] for row in sql(db, "SELECT actor FROM subject_change_event")] == [
        "tester@example.test"
    ]
    disabled = create_app(
        RunRepository(db),
        allowed_origin=origin,
        subject_management_enabled=False,
        subject_service=service,
    ).test_client()
    assert (
        disabled.get("/api/features", headers=headers).json["subject_management"]
        is False
    )
    assert disabled.get("/api/subjects", headers=headers).status_code == 503
    assert (
        disabled.post(
            "/api/subjects/" + sid + "/combine", json={}, headers=headers
        ).status_code
        == 503
    )


def test_verified_historical_model_preserves_original_labels(db):
    from scripts.verify_historical_model import verify_job

    sid, run, _groups, *_ = enroll(db)
    source = str(
        sql(
            db,
            "SELECT source_id FROM subject_example JOIN submission_face_group ON submission_group_id=group_id WHERE run_id=%s",
            (run,),
        )[0][0]
    )
    old_job, new_job = str(uuid.uuid4()), str(uuid.uuid4())
    for job, model in [(old_job, "generic-model"), (new_job, "model")]:
        sql(
            db,
            """INSERT INTO processing_job(job_id,idempotency_key,source_id,worker_version,
            detector_version,embedding_model_version,status)
            VALUES (%s,%s,%s,'worker','detector',%s,'succeeded')""",
            (job, job, source, model),
        )
        sql(
            db,
            """INSERT INTO face_track(source_id,processing_job_id,local_track_id,
            start_ms,end_ms,aggregate_embedding,model_version,observation_count,embedded_count,decision)
            VALUES (%s,%s,1,0,1000,%s::vector,%s,1,1,'matched')""",
            (source, job, pgvector(vector()), model),
        )
    service = SubjectManagement(db)
    from worker.subject_management import recalculate_subjects

    with service.transaction(write=True) as cursor:
        result = verify_job(cursor, old_job, "model", "administrator")
    assert result["verified_tracks"] == 1
    sql(
        db,
        """INSERT INTO subject_example(example_id,subject_id,source_id,face_track_id,embedding,model_version,start_ms,end_ms,quality_score)
       SELECT track_id,%s,source_id,track_id,aggregate_embedding,'model',start_ms,end_ms,max_quality
       FROM face_track WHERE processing_job_id=ANY(%s::uuid[])""",
        (sid, [old_job, new_job]),
    )
    with service.transaction(write=True) as cursor:
        recalculate_subjects(cursor, [sid])
    assert service.subject(sid)["model_version"] == "model"
    assert service.subject(sid)["example_count"] == 3
    assert service.coverage()["model_mismatches"] == 0
    assert (
        sql(
            db,
            "SELECT count(*) FROM face_track WHERE model_version='generic-model'",
        )[0][0]
        == 1
    )
    sql(
        db,
        "UPDATE face_track SET aggregate_embedding=%s::vector WHERE processing_job_id=%s",
        (pgvector(vector(1.0)), new_job),
    )
    with (
        service.transaction(write=True) as cursor,
        pytest.raises(ValueError, match="bit-identical"),
    ):
        verify_job(cursor, old_job, "model", "administrator")


def test_manual_enrollment_never_associates_identical_tracks(db):
    existing, *_ = enroll(db)
    run, groups = make_run(db, 28)
    repository = RunRepository(db)
    repository.select(run, groups, 1)
    assert (
        sql(db, "SELECT count(*) FROM enrollment_assignment WHERE run_id=%s", (run,))[
            0
        ][0]
        == 28
    )
    worker, _ = processor(db)
    worker.repository.rank = lambda *args: pytest.fail("Enrollment must not rank faces")
    assert worker.match(run)
    destinations = {
        str(r[0])
        for r in sql(
            db,
            "SELECT subject_id FROM subject_example JOIN submission_face_group ON submission_group_id=group_id WHERE run_id=%s",
            (run,),
        )
    }
    assert len(destinations) == 28 and existing not in destinations
    assert SubjectManagement(db).subject(existing)["example_count"] == 1
    assert repository.select(run, groups, 1)["state"] == "succeeded"
    assert SubjectManagement(db).coverage()["count_mismatches"] == 0


def test_source_filter_sort_pagination_and_source_examples(db):
    service = SubjectManagement(db)
    primary, *_ = enroll(db, 2)
    for _ in range(2):
        other, *_ = enroll(db)
        service.combine(
            primary,
            {
                "operation_id": str(uuid.uuid4()),
                "version": service.subject(primary)["version"],
                "other_subject_id": other,
                "target_version": service.subject(other)["version"],
            },
            "tester",
        )
    secondary, *_ = enroll(db)
    extra, *_ = enroll(db)
    service.combine(
        secondary,
        {
            "operation_id": str(uuid.uuid4()),
            "version": service.subject(secondary)["version"],
            "other_subject_id": extra,
            "target_version": service.subject(extra)["version"],
        },
        "tester",
    )
    single, *_ = enroll(db)
    first = service.subjects(multiple_sources=True, sort="sources", limit=1)
    assert (
        first["subjects"][0]["subject_id"] == primary
        and first["subjects"][0]["source_count"] == 3
    )
    second = service.subjects(
        multiple_sources=True, sort="sources", limit=1, after=first["next_cursor"]
    )
    assert (
        second["subjects"][0]["subject_id"] == secondary
        and second["next_cursor"] is None
    )
    assert service.subjects(q=single, multiple_sources=True)["subjects"] == []
    for sort, cursor in [
        ("sources", "bad"),
        ("sources", "-1:" + primary),
        ("invalid", None),
    ]:
        with pytest.raises(SubjectError):
            service.subjects(sort=sort, after=cursor)
    sources = service.sources(primary)
    assert sources["version"] == service.subject(primary)["version"]
    assert sum(r["example_count"] for r in sources["sources"]) == 4
    source = next(r for r in sources["sources"] if r["example_count"] == 2)
    page = service.examples(primary, source_id=source["source_id"], limit=1)
    following = service.examples(
        primary, source_id=source["source_id"], limit=1, after=page["next_cursor"]
    )
    assert len(page["examples"]) == len(following["examples"]) == 1
    assert page["examples"][0]["example_id"] != following["examples"][0]["example_id"]
    assert following["next_cursor"] is None
    assert all(
        e["source_id"] == source["source_id"]
        for e in [*page["examples"], *following["examples"]]
    )


def test_browser_source_selection_crosses_pages_and_previews_all_sources(
    db, browser_console
):
    from playwright.sync_api import expect

    page, origin = browser_console
    sid, *_ = enroll(db, 31)
    other, *_ = enroll(db, 9)
    service = SubjectManagement(db)
    service.combine(
        sid,
        {
            "operation_id": str(uuid.uuid4()),
            "version": service.subject(sid)["version"],
            "other_subject_id": other,
            "target_version": service.subject(other)["version"],
        },
        "tester",
    )
    destination, *_ = enroll(db)
    page.goto(origin + "/subjects?multiple_sources=true&sort=sources")
    expect(page.get_by_label("Multiple sources")).to_be_checked()
    expect(page.get_by_label("Sort subjects")).to_have_value("sources")
    expect(page.locator(".subject-card")).to_have_count(1)
    page.get_by_role("link", name="Open subject").click()
    expect(page.locator(".example-row")).to_have_count(30)
    video_source = next(
        r for r in service.sources(sid)["sources"] if r["example_count"] == 31
    )["source_id"]
    section = page.locator(".example-source").filter(
        has=page.get_by_role("heading", name="Source " + video_source, exact=True)
    )
    section.get_by_role(
        "button", name="Select this source's examples", exact=True
    ).click()
    expect(
        page.get_by_role("button", name="Move 31 selected examples", exact=True)
    ).to_be_enabled()
    page.get_by_role("button", name="Merge subjects").click()
    page.locator(".correction-panel").get_by_label("Subject ID or display name").fill(
        destination
    )
    page.locator(".correction-panel").get_by_role(
        "button", name="Search Subjects", exact=True
    ).click()
    page.locator(".correction-panel").get_by_label("Select for merge").check()
    page.locator(".correction-panel").get_by_role("button", name="Review selected merge").click()
    dialog = page.get_by_role("dialog")
    expect(dialog).to_contain_text(
        "All 41 examples from 3 distinct sources will be merged."
    )
    expect(
        dialog.get_by_role("button", name="Merge entire subjects", exact=True)
    ).to_be_visible()
    if os.getenv("FACE_BROWSER_SCREENSHOTS"):
        directory = Path(os.environ["FACE_BROWSER_SCREENSHOTS"])
        directory.mkdir(parents=True, exist_ok=True)
        page.screenshot(
            path=str(directory / "manual-merge-desktop.png"), full_page=True
        )
        page.set_viewport_size({"width": 390, "height": 844})
        page.screenshot(path=str(directory / "manual-merge-mobile.png"), full_page=True)
        assert page.evaluate(
            "document.documentElement.scrollWidth <= window.innerWidth"
        )
        page.set_viewport_size({"width": 1280, "height": 900})
    dialog.get_by_role("button", name="Cancel", exact=True).click()
    page.locator(".correction-panel").get_by_role(
        "button", name="Close", exact=True
    ).click()
    page.get_by_role("button", name="Move 31 selected examples").click()
    page.get_by_role("button", name="Create a new subject", exact=True).click()
    dialog = page.get_by_role("dialog")
    expect(dialog).to_contain_text(
        "Only 31 selected examples from 1 sources will move."
    )
    expect(dialog).to_contain_text(
        "Original subject after move: 9 examples, 1 sources."
    )
    dialog.get_by_role("button", name="Move examples", exact=True).click()
    expect(page).not_to_have_url(origin + "/subjects/" + sid)
    assert service.subject(sid)["example_count"] == 9
    assert service.subject(sid)["source_count"] == 1
    assert service.subject(page.url.split("/")[-1])["example_count"] == 31
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


@pytest.mark.parametrize(
    "theme,width", [("light", 390), ("dark", 390), ("light", 1280), ("dark", 1280)]
)
def test_browser_subject_controls_layout(db, browser_console, theme, width):
    from playwright.sync_api import expect

    page, origin = browser_console
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(origin + "/subjects?multiple_sources=true&sort=sources")
    page.evaluate("theme => setTheme(theme)", theme)
    checkbox = page.get_by_label("Multiple sources")
    expect(checkbox).to_be_checked()
    order = page.get_by_label("Sort subjects")
    expect(order).to_have_value("sources")
    geometry = page.evaluate("""() => {
        const box = selector => {
            const r = document.querySelector(selector).getBoundingClientRect();
            return {x:r.x, y:r.y, width:r.width, height:r.height};
        };
        const label = document.querySelector('.subject-filter-inline');
        const range = document.createRange(); range.selectNodeContents(label.lastChild);
        const text = range.getBoundingClientRect();
        return {checkbox:box('.subject-filter-inline input'), text:{y:text.y,height:text.height},
            label:box('.subject-sort-inline label'), select:box('.subject-sort-inline select'),
            button:box('.subject-search-button'), container:box('#subject-browser'),
            scroll:document.documentElement.scrollWidth, viewport:innerWidth};
    }""")
    center = lambda r: r["y"] + r["height"] / 2
    assert abs(center(geometry["checkbox"]) - center(geometry["text"])) < 3
    assert abs(center(geometry["label"]) - center(geometry["select"])) < 3
    assert geometry["select"]["x"] > geometry["label"]["x"] + geometry["label"]["width"]
    assert abs(geometry["button"]["width"] - geometry["container"]["width"]) < 1
    assert geometry["scroll"] <= geometry["viewport"]
    checkbox.focus()
    page.keyboard.press("Tab")
    expect(page.get_by_label("Multiple Subjects per Source", exact=True)).to_be_focused()
    page.keyboard.press("Tab")
    expect(order).to_be_focused()
    page.keyboard.press("Tab")
    expect(
        page.get_by_role("button", name="Search Subjects", exact=True)
    ).to_be_focused()
    artifacts = os.getenv("FACE_BROWSER_ARTIFACTS")
    if artifacts:
        Path(artifacts).mkdir(parents=True, exist_ok=True)
        page.screenshot(
            path=str(Path(artifacts) / f"subject-controls-{theme}-{width}.png")
        )


def test_browser_recent_runs_navigation_and_stale_responses(db, browser_console):
    from playwright.sync_api import expect

    page, origin = browser_console
    pending = []
    page.route("**/api/runs/recent", lambda route: pending.append(route))
    page.goto(origin + "/")
    expect(page.locator("#submit .recent")).to_have_count(0)
    assert pending == []
    check = page.get_by_role("button", name="Check a run", exact=True)
    check.click()
    expect(page.locator("#recent-runs")).to_have_text("Loading recent runs…")
    page.wait_for_function("location.search === '?view=check'")
    # A new visit must invalidate the previous in-flight response.
    page.evaluate("show('subjects')")
    check.click()
    page.wait_for_timeout(100)
    assert len(pending) == 2
    pending[1].fulfill(json={"runs": []})
    expect(page.locator("#recent-runs")).to_have_text("No recent runs.")
    pending[0].fulfill(status=503, json={"message": "old request failed"})
    expect(page.locator("#recent-runs")).to_have_text("No recent runs.")
    check.click()
    page.wait_for_timeout(100)
    pending[-1].fulfill(status=503, json={"message": "unavailable"})
    expect(page.locator("#recent-runs")).to_contain_text("temporarily unavailable")
    expect(page.get_by_label("Run ID", exact=True)).to_be_enabled()
    run_id = str(uuid.uuid4())
    page.route(
        "**/api/runs/" + run_id,
        lambda route: route.fulfill(
            json={
                "run_id": run_id,
                "state": "cancelled",
                "expires_at": "2026-09-10T00:00:00Z",
            }
        ),
    )
    page.get_by_label("Run ID", exact=True).fill(run_id)
    page.get_by_role("button", name="Check status", exact=True).click()
    expect(page.locator("#error-message")).to_have_text("Run cancelled.")
    check.click()
    page.wait_for_timeout(100)
    pending[-1].fulfill(
        json={
            "runs": [
                {
                    "run_id": run_id,
                    "state": "cancelled",
                    "created_at": "2026-09-09T00:00:00Z",
                    "status_url": "/runs/" + run_id,
                }
            ]
        }
    )
    page.locator(".recent-run").click()
    expect(page.locator("#error-message")).to_have_text("Run cancelled.")
    expect(page).to_have_url(origin + "/runs/" + run_id)


def test_preservation_receipt_detects_membership_and_representation_changes(db):
    from maintenance.migrations import MigrationError
    from maintenance.preservation import capture, verify

    subject, *_ = enroll(db, 2)
    connection = db.connect()
    try:
        before = capture(connection)
        assert not any(before["violations"].values())
        assert before["records"]["examples"]["rows"] == 2
        verify(before, capture(connection))
        # An accidental representation edit is caught even with intact examples.
        sql(
            db,
            "UPDATE subject SET canonical_embedding=%s::vector WHERE subject_id=%s",
            (pgvector(vector(1.0)), subject),
        )
        after = capture(connection)
        assert after["violations"]["representations"] == 1
        with pytest.raises(MigrationError, match="subjects"):
            verify(before, after)
        assert after["records"]["examples"] == before["records"]["examples"]
    finally:
        connection.close()


def test_consistent_read_only_copy_preserves_all_tables_and_constraints(db, tmp_path):
    from maintenance.copy_database import copy_database
    from maintenance.migrations import ordered_migrations
    from maintenance.preservation import capture, verify

    enroll(db, 3)
    admin = db.connect()
    admin.autocommit = True
    name = "copy_test_" + uuid.uuid4().hex
    admin.cursor().execute(f'CREATE DATABASE "{name}"')
    source, target = db.connect(), None
    try:
        target = pg8000.connect(
            user="postgres",
            host="127.0.0.1",
            port=int(os.environ["FACE_SUBJECT_TEST_PORT"]),
            database=name,
        )
        before = capture(source)
        receipt = copy_database(source, target, ordered_migrations(Path(".")), tmp_path)
        assert receipt["source_mode"] == "repeatable-read/read-only"
        assert receipt["constraints"] == "enforced"
        assert receipt["tables"]["subject_example"]["bytes"] > 0
        verify(before, capture(target))
        verify(before, capture(source))
        # The copied database's FK constraints remain enabled and effective.
        with pytest.raises(pg8000.DatabaseError):
            target.cursor().execute(
                "UPDATE subject_example SET subject_id=%s", (str(uuid.uuid4()),)
            )
        target.rollback()
    finally:
        source.close()
        if target:
            target.close()
        admin.cursor().execute(f'DROP DATABASE "{name}" WITH (FORCE)')
        admin.close()


def test_potential_matches_rank_dismiss_restore_versions_and_ownership(db):
    service = SubjectManagement(db)
    origin = enroll(db)[0]
    candidates = sorted(enroll(db)[0] for _ in range(12))
    # Empty and incompatible active subjects are never suggestions.
    empty = str(uuid.uuid4())
    sql(
        db,
        "INSERT INTO subject(subject_id,model_version,sample_count) VALUES (%s,'model',0)",
        (empty,),
    )
    incompatible = enroll(db, angle=0.3)[0]
    sql(
        db,
        "UPDATE subject SET model_version='different-model' WHERE subject_id=%s",
        (incompatible,),
    )
    merged = enroll(db)[0]
    service.combine(
        origin,
        {
            "operation_id": str(uuid.uuid4()),
            "version": service.subject(origin)["version"],
            "other_subject_id": merged,
            "target_version": service.subject(merged)["version"],
        },
        "reviewer",
    )
    before = sql(
        db, "SELECT example_id,subject_id FROM subject_example ORDER BY example_id"
    )
    found = service.potential_matches(origin)
    assert [c["subject_id"] for c in found["candidates"]] == candidates[:10]
    assert all(abs(c["similarity"] - 1) < 1e-6 for c in found["candidates"])
    assert all(
        c["example_count"] == 1 and c["source_count"] == 1 for c in found["candidates"]
    )
    target = candidates[0]
    body = {
        "operation_id": str(uuid.uuid4()),
        "version": found["version"],
        "target_version": service.subject(target)["version"],
    }
    dismissed = service.suggestion_dismissal(origin, target, body, "reviewer")
    assert service.suggestion_dismissal(origin, target, body, "reviewer") == dismissed
    assert [
        c["subject_id"] for c in service.potential_matches(origin)["candidates"]
    ] == candidates[1:11]
    assert [
        c["subject_id"] for c in service.potential_matches(origin, True)["candidates"]
    ] == [target]
    assert origin in [
        c["subject_id"] for c in service.potential_matches(target, True)["candidates"]
    ]
    reverse = {
        "operation_id": str(uuid.uuid4()),
        "version": body["target_version"],
        "target_version": body["version"],
    }
    service.suggestion_dismissal(
        target, origin, reverse, "second-reviewer", restore=True
    )
    assert service.potential_matches(origin, True)["candidates"] == []
    assert (
        service.suggestion_dismissal(
            target, origin, reverse, "second-reviewer", restore=True
        )["dismissed"]
        is False
    )
    body["operation_id"] = str(uuid.uuid4())
    service.suggestion_dismissal(origin, target, body, "reviewer")
    detail = service.subject(target)
    service.edit(
        target,
        {
            "operation_id": str(uuid.uuid4()),
            "version": detail["version"],
            "identity_version": detail["identity_version"],
            "display_name": "Edited candidate",
            "external_identity_ref": None,
        },
        "reviewer",
    )
    assert service.potential_matches(origin, True)["candidates"] == []
    assert service.potential_matches(origin)["candidates"][0]["subject_id"] == target
    with pytest.raises(SubjectError) as stale:
        service.suggestion_dismissal(origin, target, body, "reviewer")
    assert stale.value.code == "stale_subject"
    assert (
        sql(db, "SELECT example_id,subject_id FROM subject_example ORDER BY example_id")
        == before
    )
    assert (
        sql(
            db,
            "SELECT count(*) FROM subject_change_event WHERE action IN ('dismiss_suggestion','restore_suggestion')",
        )[0][0]
        == 3
    )
    with pytest.raises(SubjectError) as combined:
        service.potential_matches(merged)
    assert combined.value.code == "subject_merged"


def test_browser_potential_matches_dismiss_restore_and_merge(db, browser_console):
    from playwright.sync_api import expect

    page, origin = browser_console
    sid, target = enroll(db)[0], enroll(db)[0]
    sql(
        db,
        "UPDATE subject_representative_face r SET active=false,retired_at=now() FROM subject_example e WHERE r.example_id=e.example_id AND e.subject_id=%s",
        (target,),
    )
    requests = []
    page.on(
        "request",
        lambda request: (
            requests.append(request.url) if "potential-matches" in request.url else None
        ),
    )
    page.goto(origin + "/subjects/" + sid)
    expect(
        page.get_by_role("button", name="Find potential matches", exact=True)
    ).to_be_visible()
    assert requests == []
    section = page.locator(".potential-matches")
    section.get_by_role("button", name="Find potential matches", exact=True).click()
    expect(section.locator(".subject-card")).to_have_count(1)
    expect(section).to_contain_text(target)
    expect(section).to_contain_text("No gallery images available.")
    expect(section).to_contain_text("Similarity: 1.0000")
    section.get_by_role("button", name="Dismiss", exact=True).click()
    expect(section.locator(".subject-card")).to_have_count(0)
    section.get_by_role("button", name="Show dismissed", exact=True).click()
    expect(section.locator(".subject-card")).to_have_count(1)
    section.get_by_role("button", name="Restore", exact=True).click()
    expect(section.locator(".subject-card")).to_have_count(0)
    section.get_by_role("button", name="Show potential matches", exact=True).click()
    expect(section.locator(".subject-card")).to_have_count(1)
    # A second dismiss at unchanged subject versions must be a new user action.
    section.get_by_role("button", name="Dismiss", exact=True).click()
    expect(section.locator(".subject-card")).to_have_count(0)
    section.get_by_role("button", name="Show dismissed", exact=True).click()
    expect(section.locator(".subject-card")).to_have_count(1)
    section.get_by_role("button", name="Review merge", exact=True).click()
    dialog = page.get_by_role("dialog")
    expect(dialog).to_contain_text("All 2 examples")
    dialog.get_by_label("Keep this subject and its identity details").select_option(
        target
    )
    dialog.get_by_role("button", name="Merge entire subjects", exact=True).click()
    expect(page).to_have_url(origin + "/subjects/" + target)
    expect(page.locator(".example-row")).to_have_count(2)
    expect(page.locator(".potential-matches .subject-card")).to_have_count(0)
    assert SubjectManagement(db).coverage()["count_mismatches"] == 0


def test_suggestion_api_auth_origin_validation_and_stale_versions(db):
    from worker.console import create_app

    sid, target = enroll(db)[0], enroll(db)[0]
    service = SubjectManagement(db)
    app = create_app(
        RunRepository(db),
        allowed_origin="https://test.example",
        subject_management_enabled=True,
        subject_service=service,
    )
    client = app.test_client()
    headers = {
        "Origin": "https://test.example",
        "X-Goog-Authenticated-User-Email": "accounts.google.com:reviewer@example.test",
    }
    url = f"/api/subjects/{sid}/potential-matches"
    mutation = url + f"/{target}/dismissal"
    assert client.get(url).status_code == 401
    assert client.get(url + "?dismissed=invalid", headers=headers).status_code == 422
    result = client.get(url, headers=headers)
    assert result.status_code == 200
    body = {
        "operation_id": str(uuid.uuid4()),
        "version": result.json["version"],
        "target_version": service.subject(target)["version"],
    }
    assert (
        client.put(
            mutation, json=body, headers={**headers, "Origin": "https://wrong.example"}
        ).status_code
        == 403
    )
    assert client.put(mutation, json=body, headers=headers).status_code == 200
    assert (
        client.delete(
            mutation, json={**body, "version": body["version"] + 1}, headers=headers
        ).status_code
        == 409
    )
    assert (
        client.delete(
            mutation, json={**body, "operation_id": str(uuid.uuid4())}, headers=headers
        ).status_code
        == 200
    )


def test_saved_results_capture_versions_and_preserve_scores_after_changes(db):
    service, runs = SubjectManagement(db), RunRepository(db)
    sid = enroll(db)[0]
    compared = service.subject(sid)["version"]
    run, groups = make_run(db, policy="search_then_discard")
    runs.select(run, groups, 1)
    worker, _ = processor(db)
    assert worker.match(run)
    candidate = runs.results(run)["groups"][0]["candidates"][0]
    assert candidate["subject_id"] == sid
    assert candidate["compared_subject_version"] == compared
    assert candidate["subject_version_status"] == "unchanged"
    snapshot = sql(
        db, "SELECT row_to_json(c) FROM run_candidate c WHERE run_id=%s", (run,)
    )
    detail = service.subject(sid)
    service.edit(
        sid,
        {
            "operation_id": str(uuid.uuid4()),
            "version": detail["version"],
            "identity_version": detail["identity_version"],
            "display_name": "Current name",
            "external_identity_ref": None,
        },
        "reviewer",
    )
    changed = runs.results(run)["groups"][0]["candidates"][0]
    assert changed["subject_version_status"] == "changed"
    assert changed["display_name"] == candidate["display_name"]
    assert changed["similarity"] == candidate["similarity"]
    assert (
        sql(db, "SELECT row_to_json(c) FROM run_candidate c WHERE run_id=%s", (run,))
        == snapshot
    )
    # A normal merge follows the subject redirect; the old result keeps its ID/score.
    survivor = enroll(db)[0]
    service.combine(
        survivor,
        {
            "operation_id": str(uuid.uuid4()),
            "version": service.subject(survivor)["version"],
            "other_subject_id": sid,
            "target_version": service.subject(sid)["version"],
        },
        "reviewer",
    )
    merged = runs.results(run)["groups"][0]["candidates"][0]
    assert (
        merged["subject_id"] == sid and merged["similarity"] == candidate["similarity"]
    )
    assert service.subject(sid)["subject_id"] == survivor
    assert (
        sql(db, "SELECT row_to_json(c) FROM run_candidate c WHERE run_id=%s", (run,))
        == snapshot
    )


def test_older_saved_results_distinguish_unavailable_and_source_split_evidence(db):
    sid = enroll(db)[0]
    run, groups = make_run(db, policy="search_then_discard")
    runs = RunRepository(db)
    runs.select(run, groups, 1)
    worker, _ = processor(db)
    assert worker.match(run)
    sql(
        db,
        "UPDATE run_candidate SET compared_subject_version=NULL WHERE run_id=%s",
        (run,),
    )
    before = runs.results(run)["groups"][0]["candidates"][0]
    assert before["subject_version_status"] == "unavailable"
    snapshot = sql(
        db, "SELECT row_to_json(c) FROM run_candidate c WHERE run_id=%s", (run,)
    )
    # This is the retained shape of the completed source-split audit receipt.
    sql(
        db,
        """INSERT INTO subject_change_event(operation_id,actor,action,request_fingerprint,details,result)
               VALUES (%s,'reviewer','move','synthetic',%s::jsonb,'{}')""",
        (
            str(uuid.uuid4()),
            json.dumps(
                {
                    "kind": "split_by_source",
                    "before_and_partitions": {"subject_id": sid},
                }
            ),
        ),
    )
    after = runs.results(run)["groups"][0]["candidates"][0]
    assert after["subject_version_status"] == "changed"
    assert (
        after["subject_id"] == before["subject_id"]
        and after["similarity"] == before["similarity"]
    )
    assert (
        sql(db, "SELECT row_to_json(c) FROM run_candidate c WHERE run_id=%s", (run,))
        == snapshot
    )


def test_retain_and_enroll_compares_versions_but_creates_new_subjects(db):
    existing = enroll(db)[0]
    before = SubjectManagement(db).subject(existing)
    enrolled, run, *_ = enroll(db, policy="retain_and_enroll")
    assert enrolled != existing
    result = RunRepository(db).results(run)["groups"][0]
    candidate = result["candidates"][0]
    assert candidate["subject_id"] == existing
    assert candidate["compared_subject_version"] == before["version"]
    assert candidate["subject_version_status"] == "unchanged"
    assert result["enrollment"]["subject_id"] == enrolled
    assert SubjectManagement(db).subject(existing)["version"] == before["version"]
    service = SubjectManagement(db)
    service.combine(
        existing,
        {
            "operation_id": str(uuid.uuid4()),
            "version": service.subject(existing)["version"],
            "other_subject_id": enrolled,
            "target_version": service.subject(enrolled)["version"],
        },
        "reviewer",
    )
    assert (
        RunRepository(db).results(run)["groups"][0]["enrollment"]["subject_id"]
        == existing
    )


def test_browser_saved_result_changed_and_unavailable_messages(db, browser_console):
    from playwright.sync_api import expect

    page, origin = browser_console
    sid = enroll(db)[0]
    run, groups = make_run(db, policy="search_then_discard")
    RunRepository(db).select(run, groups, 1)
    worker, _ = processor(db)
    assert worker.match(run)
    page.goto(origin + "/runs/" + run)
    expect(page.locator(".subject-result-link")).to_contain_text(sid)
    expect(page.locator(".subject-version-note")).to_have_count(0)
    sql(
        db,
        "UPDATE run_candidate SET compared_subject_version=NULL WHERE run_id=%s",
        (run,),
    )
    page.reload()
    expect(page.locator(".subject-version-note")).to_have_text(
        "Prior subject version is unavailable"
    )
    service = SubjectManagement(db)
    detail = service.subject(sid)
    service.edit(
        sid,
        {
            "operation_id": str(uuid.uuid4()),
            "version": detail["version"],
            "identity_version": detail["identity_version"],
            "display_name": "Updated identity",
            "external_identity_ref": None,
        },
        "reviewer",
    )
    page.reload()
    expect(page.locator(".subject-version-note")).to_have_text(
        "Subject changed since this search"
    )
    expect(page.locator(".subject-result-link")).to_contain_text(sid)


def test_browser_suggestion_retry_stale_refresh_and_layout(db, browser_console):
    from playwright.sync_api import expect

    page, origin = browser_console
    sid, target = enroll(db)[0], enroll(db)[0]
    pending = []
    pattern = "**/potential-matches?dismissed=false"
    page.route(pattern, lambda route: pending.append(route))
    page.goto(origin + "/subjects/" + sid)
    page.get_by_role("button", name="Find potential matches", exact=True).click()
    section = page.locator(".potential-matches")
    expect(section).to_contain_text("Loading potential matches…")
    page.wait_for_timeout(100)
    assert len(pending) == 1
    pending.pop().fulfill(
        status=503,
        content_type="application/json",
        body='{"message":"Comparison temporarily unavailable"}',
    )
    expect(section).to_contain_text("Comparison temporarily unavailable")
    page.unroute(pattern)
    section.get_by_role("button", name="Retry", exact=True).click()
    expect(section.locator(".subject-card")).to_have_count(1)
    service = SubjectManagement(db)
    detail = service.subject(target)
    service.edit(
        target,
        {
            "operation_id": str(uuid.uuid4()),
            "version": detail["version"],
            "identity_version": detail["identity_version"],
            "display_name": "Fresh candidate details",
            "external_identity_ref": None,
        },
        "reviewer",
    )
    section.get_by_role("button", name="Dismiss", exact=True).click()
    expect(page.locator(".potential-matches")).to_contain_text(
        "Fresh candidate details"
    )
    assert service.potential_matches(sid, True)["candidates"] == []
    artifacts = os.getenv("FACE_BROWSER_ARTIFACTS")
    for width in [1280, 390]:
        page.set_viewport_size({"width": width, "height": 850})
        assert page.evaluate(
            "document.documentElement.scrollWidth <= window.innerWidth"
        )
        if artifacts:
            page.screenshot(
                path=str(Path(artifacts) / f"potential-matches-{width}.png"),
                full_page=True,
            )


def test_operator_receipt_retry_and_unattended_archive_run(db, tmp_path):
    from worker.ingest_drain import drain
    from worker.ingest_repository import IngestRepository
    from worker.operator_submission import prepare, submit

    receipt = tmp_path / "receipt.json"
    payload = {
        "handling_policy": "enroll_only",
        "selection_policy": "all_tracks",
        "source": {
            "kind": "archive",
            "bucket": "test-archive",
            "object_name": "sources/example.mp4",
            "generation": 8,
            "sha256": "a" * 64,
            "bytes": 100,
            "content_type": "video/mp4",
            "page_url": None,
        },
    }
    prepare(receipt, "operator", payload)
    runs = RunRepository(db)

    class LostAcknowledgement:
        def create(self, *args):
            runs.create(*args)
            raise ConnectionError("response lost after database commit")

    with pytest.raises(ConnectionError):
        submit(receipt, LostAcknowledgement())
    assert json.loads(receipt.read_text())["run_id"] is None
    run = submit(receipt, runs)["run_id"]
    assert submit(receipt, runs)["run_id"] == run
    assert sql(db, "SELECT count(*) FROM media_run")[0][0] == 1
    assert list(
        sql(db, "SELECT kind,state FROM run_operation WHERE run_id=%s", (run,))
    ) == [["fetch", "queued"]]

    class ArchiveStorage:
        def archive_to_temporary(self, work, limit):
            assert (
                work.archive_bucket == "test-archive" and work.archive_generation == 8
            )
            assert work.archive_sha256 == "a" * 64 and work.expected_bytes == 100
            return f"submissions-temporary/{run}/source.mp4", 9, 100

    invoked = []
    assert (
        drain(
            IngestRepository(db),
            ArchiveStorage(),
            max_bytes=1000,
            invoke_detect=invoked.append,
        )
        == 1
    )
    assert invoked == [run]
    interactive = InteractiveRepository(db)
    work = interactive.claim_detection(run)
    assert work is not None
    groups = [
        {
            "group_id": str(uuid.uuid4()),
            "local_group_id": i,
            "bbox": None,
            "start_ms": 0,
            "end_ms": 100,
            "quality": {"max_quality": 0.8},
            "preview_object_name": f"submissions-temporary/{run}/preview-{i}.jpg",
            "preview_generation": 1,
            "representative_object_name": f"submissions-temporary/{run}/representative-{i}.jpg",
            "representative_generation": 1,
            "embedding": vector(),
        }
        for i in range(2)
    ]
    assert interactive.complete_detection(
        work, groups, detector_version="detector", embedding_version="model"
    )
    assert runs.get(run)["state"] == "matching"
    assert (
        sql(db, "SELECT count(*) FROM enrollment_assignment WHERE run_id=%s", (run,))[
            0
        ][0]
        == 2
    )
    assert not interactive.complete_detection(
        work, groups, detector_version="detector", embedding_version="model"
    )
    processor(db)[0].match(run)
    assert runs.get(run)["state"] == "succeeded"
    assert sql(db, "SELECT count(DISTINCT subject_id) FROM subject_example")[0][0] == 2
    assert SubjectManagement(db).coverage()["count_mismatches"] == 0
    assert submit(receipt, runs)["run_id"] == run
    assert sql(db, "SELECT to_regclass('processing_work_item')")[0][0] is None


def test_retained_source_cleanup_keeps_explicit_bucket(db):
    from worker.maintenance import MaintenanceRepository

    _sid, run, *_ = enroll(db, policy="retain_and_enroll")
    source = str(
        sql(db, "SELECT retained_source_id FROM media_run WHERE run_id=%s", (run,))[0][
            0
        ]
    )
    RunRepository(db).tombstone_source(source, "reviewer")
    cleanup = MaintenanceRepository(db)
    retained = []
    while item := cleanup.claim_cleanup():
        if item.object_name.startswith("training-media/"):
            retained.append(item)
        cleanup.finish_cleanup(item)
    assert len(retained) == 1
    assert retained[0].object_bucket == "test-bucket"
    assert retained[0].generation == 1


def test_expiry_of_no_face_upload_and_url_preserves_required_attribution(db):
    from worker.maintenance import MaintenanceRepository

    for kind, url in [("upload", None), ("url", "https://example.com/source.mp4")]:
        rid = str(uuid.uuid4())
        sql(
            db,
            """INSERT INTO media_run(run_id,submitter_principal,idempotency_key,
               request_fingerprint,handling_policy,source_kind,source_page_url,state,
               outcome,object_name,object_generation,expires_at)
               VALUES (%s,'expiry-test',%s,%s,'search_then_discard',%s,%s,
                       'succeeded','no_faces',%s,7,now()-interval '1 second')""",
            (rid, rid, "a" * 64, kind, url, f"submissions-temporary/{rid}/source.mp4"),
        )
    repository = MaintenanceRepository(db)
    assert repository.expire_runs() == 2
    rows = sql(
        db,
        "SELECT state,outcome,retryable,source_kind,source_page_url FROM media_run ORDER BY source_kind",
    )
    assert list(rows) == [
        ["expired", None, False, "upload", None],
        ["expired", None, False, "url", "https://example.com/source.mp4"],
    ]
    assert (
        sql(db, "SELECT count(*) FROM run_cleanup_object WHERE object_generation=7")[0][
            0
        ]
        == 2
    )
    assert repository.expire_runs() == 0
    assert sql(db, "SELECT count(*) FROM run_cleanup_object")[0][0] == 2


@pytest.mark.parametrize("policy", ["enroll_only", "retain_and_enroll"])
def test_merge_preserves_every_source_gallery_and_run_enrollment(db, policy):
    existing, *_ = enroll(db, 5)
    incoming, run, _groups, *_ = enroll(db, 7, policy=policy)
    service = SubjectManagement(db)
    assert len(service.subject(incoming)["representative_faces"]) == 5
    assert all(row["preview_url"] for row in service.examples(incoming)["examples"])
    service.combine(
        existing,
        {
            "operation_id": str(uuid.uuid4()),
            "version": service.subject(existing)["version"],
            "other_subject_id": incoming,
            "target_version": service.subject(incoming)["version"],
        },
        "reviewer",
    )
    examples = service.examples(existing)["examples"]
    assert len(examples) == 12
    assert all(row["preview_url"] for row in examples)
    assert len(service.subject(existing)["representative_faces"]) == 5
    assert sql(db, "SELECT count(*) FROM gallery_cleanup_object")[0][0] == 0
    results = RunRepository(db).results(run)
    assert results["handling_policy"] == policy
    assert len(results["groups"]) == 7
    for group in results["groups"]:
        assert group["enrollment"]["subject_id"] == existing
        assert len(group["enrollment"]["representative_faces"]) == 1
    assert bool(results["groups"][0]["candidates"]) == (policy == "retain_and_enroll")


@pytest.mark.parametrize("policy", ["enroll_only", "retain_and_enroll"])
def test_browser_enrollment_gallery_and_matching_status(db, browser_console, policy):
    from playwright.sync_api import expect

    page, origin = browser_console
    enroll(db)
    sid, run, *_ = enroll(db, 7, policy=policy)
    page.goto(origin + "/runs/" + run)
    expect(page.locator(".enrolled-subject")).to_have_count(1)
    expect(page.locator(".enrolled-subject a")).to_have_attribute(
        "href", "/subjects/" + sid
    )
    expect(page.locator('.enrolled-subject h3')).to_have_text('Enrolled ' + sid)
    expect(page.locator('.enrolled-subject a')).to_have_text('View Subject')
    assert page.locator('.enrolled-subject a').bounding_box()['y'] > page.locator('.enrolled-subject .representatives').bounding_box()['y']
    images = page.locator(".enrolled-subject img")
    expect(images).to_have_count(7)
    expect(images.first).to_be_visible()
    page.wait_for_function(
        "[...document.querySelectorAll('.enrolled-subject img')].every(i => i.complete && i.naturalWidth > 0)"
    )
    if policy == "enroll_only":
        expect(page.locator(".matching-status")).to_contain_text(
            "matching against existing subjects was not run"
        )
        expect(page.locator(".candidate")).to_have_count(0)
    else:
        expect(page.locator(".candidate")).to_have_count(7)
        expect(page.locator(".matching-status")).to_have_count(0)


def test_bulk_merge_atomic_replay_and_separate_previous_member(db):
    service = SubjectManagement(db)
    ids = [enroll(db, n)[0] for n in (2, 3, 4)]
    before = {sid: service.subject(sid) for sid in ids}
    origins = sql(db, 'SELECT example_id,source_id FROM subject_example ORDER BY example_id')
    data = {'operation_id': str(uuid.uuid4()), 'version': before[ids[0]]['version'],
            'subjects': [{'subject_id': sid, 'version': before[sid]['version']} for sid in ids[1:]]}
    invalid = {**data, 'subjects': [data['subjects'][0], {**data['subjects'][1], 'version': -1}]}
    with pytest.raises(SubjectError) as error:
        service.bulk_combine(ids[0], invalid, 'tester')
    assert error.value.code == 'stale_subject'
    assert [service.subject(s)['example_count'] for s in ids] == [2, 3, 4]
    result = service.bulk_combine(ids[0], data, 'tester')
    assert result['destination']['example_count'] == 9
    assert service.bulk_combine(ids[0], data, 'tester') == json.loads(json.dumps(result, default=str))
    assert len(sql(db, 'SELECT * FROM subject_change_event')) == 1
    with pytest.raises(SubjectError):
        service.bulk_combine(ids[0], data, 'other-actor')
    history = service.merge_members(ids[0])
    assert len(history['members']) == 2 and all(m['can_separate'] for m in history['members'])
    separation = {'operation_id': str(uuid.uuid4()), 'version': result['destination']['version'],
                  'merge_operation_id': data['operation_id'], 'member_subject_id': ids[1]}
    restored = service.separate_merge(ids[0], separation, 'tester')
    assert restored['destination']['subject_id'] == ids[1]
    assert restored['destination']['example_count'] == 3
    assert restored['subject']['example_count'] == 6
    assert service.separate_merge(ids[0], separation, 'tester') == json.loads(json.dumps(restored, default=str))
    assert sql(db, 'SELECT example_id,source_id FROM subject_example ORDER BY example_id') == origins
    assert service.coverage()['count_mismatches'] == 0
    assert not next(m for m in service.merge_members(ids[0])['members'] if m['subject_id'] == ids[1])['can_separate']


def test_bulk_separation_refuses_changed_membership(db):
    service = SubjectManagement(db)
    left, right = enroll(db)[0], enroll(db, 2)[0]
    data = {'operation_id': str(uuid.uuid4()), 'version': service.subject(left)['version'],
            'subjects': [{'subject_id': right, 'version': service.subject(right)['version']}]}
    moved = service.examples(right)['examples'][0]['example_id']
    merged = service.bulk_combine(left, data, 'tester')
    service.move(left, {'operation_id': str(uuid.uuid4()), 'version': merged['destination']['version'],
                       'example_ids': [moved], 'target_subject_id': None, 'target_version': None}, 'tester')
    assert not service.merge_members(left)['members'][0]['can_separate']
    with pytest.raises(SubjectError) as error:
        service.separate_merge(left, {'operation_id': str(uuid.uuid4()), 'version': service.subject(left)['version'],
                                    'merge_operation_id': data['operation_id'], 'member_subject_id': right}, 'tester')
    assert error.value.code == 'examples_changed'


def test_shared_source_filter_tracks_current_membership_and_pagination(db):
    service = SubjectManagement(db)
    primary = enroll(db, 3)[0]
    unrelated = enroll(db)[0]
    source = service.sources(primary)['sources'][0]['source_id']
    example = service.examples(primary)['examples'][0]['example_id']
    split = service.move(primary, {'operation_id': str(uuid.uuid4()), 'version': service.subject(primary)['version'],
                                  'example_ids': [example], 'target_subject_id': None, 'target_version': None}, 'tester')['destination']['subject_id']
    first = service.subjects(shared_source=True, limit=1)
    second = service.subjects(shared_source=True, limit=1, after=first['next_cursor'])
    assert {s['subject_id'] for s in first['subjects'] + second['subjects']} == {primary, split}
    assert second['next_cursor'] is None
    assert service.subjects(q=unrelated, shared_source=True)['subjects'] == []
    assert len(service.subjects(q=primary, shared_source=True)['subjects']) == 1
    assert service.subjects(q=unrelated, source_id=source)['subjects'] == []
    assert len(service.subjects(source_id=source)['subjects']) == 2
    assert service.sources(primary)['sources'][0]['subject_count'] == 2
    assert service.browse_sources(multiple_subjects=True)['sources'][0]['source_id'] == source
    assert service.browse_sources(q=source)['sources'][0]['subject_count'] == 2
    page = service.browse_sources(limit=1)
    next_page = service.browse_sources(limit=1, after=page['next_cursor'])
    assert len(page['sources'] + next_page['sources']) == 2
    assert next_page['next_cursor'] is None

    service.bulk_combine(primary, {'operation_id': str(uuid.uuid4()), 'version': service.subject(primary)['version'],
                                  'subjects': [{'subject_id': split, 'version': service.subject(split)['version']}]}, 'tester')
    assert service.subjects(shared_source=True)['subjects'] == []
    assert len(service.subjects(source_id=source)['subjects']) == 1
    assert service.browse_sources(multiple_subjects=True)['sources'] == []


def test_browser_bulk_merge_source_and_separate(db, browser_console):
    from playwright.sync_api import expect
    page, origin = browser_console
    service = SubjectManagement(db)
    primary = enroll(db, 3)[0]
    source = service.sources(primary)['sources'][0]['source_id']
    examples = service.examples(primary)['examples']
    for example in examples[:2]:
        service.move(primary, {'operation_id': str(uuid.uuid4()), 'version': service.subject(primary)['version'],
                              'example_ids': [example['example_id']], 'target_subject_id': None, 'target_version': None}, 'tester')
    page.goto(origin + '/subjects')
    page.get_by_label('Multiple Subjects per Source', exact=True).check()
    expect(page.locator('#subject-browser .subject-card')).to_have_count(3)
    expect(page).to_have_url(origin + '/subjects?shared_source=true')
    page.reload()
    expect(page.get_by_label('Multiple Subjects per Source', exact=True)).to_be_checked()
    page.get_by_role('link', name='Open Source (3 subjects)', exact=True).first.click()
    expect(page).to_have_url(origin + '/sources/' + source)
    page.get_by_role('link', name='Sources', exact=True).click()
    page.get_by_label('Multiple subjects', exact=True).check()
    expect(page.locator('#subject-detail .subject-card')).to_have_count(1)
    page.set_viewport_size({'width': 390, 'height': 844})
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    page.reload()
    expect(page.get_by_label('Multiple subjects', exact=True)).to_be_checked()
    page.get_by_role('link', name='Open Source (3 subjects)', exact=True).click()
    expect(page.locator('#subject-detail .subject-card')).to_have_count(3)
    checks = page.get_by_label('Select for merge', exact=True)
    checks.nth(0).click()
    checks.nth(2).click(modifiers=['Shift'])
    expect(page.get_by_text('3 subjects selected', exact=True)).to_be_visible()
    checks.nth(0).click(modifiers=['Shift'])
    expect(page.get_by_text('0 subjects selected', exact=True)).to_be_visible()
    page.get_by_role('button', name='Select all', exact=True).click()
    expect(page.get_by_text('3 subjects selected', exact=True)).to_be_visible()
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    search_box = page.get_by_role('button', name='Search Subjects', exact=True).bounding_box()
    merge_box = page.get_by_role('button', name='Merge', exact=True).bounding_box()
    assert merge_box['y'] > search_box['y'] + search_box['height']
    page.get_by_label('Keep subject', exact=True).select_option(primary)
    page.get_by_role('button', name='Merge', exact=True).click()
    expect(page.get_by_role('dialog')).to_have_count(0)
    expect(page).to_have_url(origin + '/subjects/' + primary)
    expect(page.locator('.example-row')).to_have_count(3)
    page.get_by_role('button', name='Separate a previous merge', exact=True).click()
    expect(page.get_by_role('button', name='Separate back out', exact=True)).to_have_count(2)
    page.get_by_role('button', name='Separate back out', exact=True).first.click()
    page.get_by_role('dialog').get_by_role('button', name='Separate subject', exact=True).click()
    expect(page).not_to_have_url(origin + '/subjects/' + primary)
    expect(page.locator('.example-row')).to_have_count(1)
    page.locator('.example-row input').check()
    page.get_by_role('button', name='Separate selected into new subject', exact=True).click()
    expect(page.get_by_role('dialog')).to_contain_text('A new subject will be created')
    page.get_by_role('dialog').get_by_role('button', name='Cancel', exact=True).click()
    page.set_viewport_size({'width': 390, 'height': 844})
    page.goto(origin + '/sources/' + source)
    expect(page.locator('#subject-detail .subject-card')).to_have_count(2)
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')


def test_browser_select_all_crosses_pages_and_respects_limit(db, browser_console):
    from playwright.sync_api import expect
    page, origin = browser_console
    service = SubjectManagement(db)
    primary = enroll(db, 13)[0]
    source = service.sources(primary)['sources'][0]['source_id']
    for example in service.examples(primary)['examples'][:12]:
        service.move(primary, {'operation_id': str(uuid.uuid4()), 'version': service.subject(primary)['version'],
                              'example_ids': [example['example_id']], 'target_subject_id': None, 'target_version': None}, 'tester')
    page.goto(origin + '/sources/' + source)
    expect(page.get_by_label('Select for merge', exact=True)).to_have_count(12)
    page.get_by_role('button', name='Select all', exact=True).click()
    expect(page.get_by_text('13 subjects selected', exact=True)).to_be_visible()
    next_button = page.get_by_role('button', name='Next', exact=True)
    grid = page.locator('.subject-grid').bounding_box()
    bounds = next_button.bounding_box()
    assert abs(bounds['width'] - (grid['width'] - 12) / 2) < 2
    assert bounds['y'] >= grid['y'] + grid['height'] + 24
    if os.getenv('FACE_BROWSER_SCREENSHOTS'):
        folder = Path(os.environ['FACE_BROWSER_SCREENSHOTS'])
        folder.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(folder / 'source-merge-desktop.png'))
        page.set_viewport_size({'width': 390, 'height': 844})
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        page.screenshot(path=str(folder / 'source-merge-mobile.png'))
    next_button.click()
    expect(page.get_by_label('Select for merge', exact=True)).to_have_count(1)
    expect(page.get_by_label('Select for merge', exact=True)).to_be_checked()
    page.get_by_role('button', name='Clear selection', exact=True).click()
    expect(page.get_by_text('0 subjects selected', exact=True)).to_be_visible()
    page.route('**/api/subjects?**limit=100**', lambda route: route.fulfill(json={
        'subjects': [{'subject_id': str(uuid.uuid4())} for _ in range(51)],
        'next_cursor': None,
    }))
    page.get_by_role('button', name='Select all', exact=True).click()
    expect(page.get_by_role('alert')).to_contain_text('Select at most 50 subjects')
    expect(page.get_by_text('0 subjects selected', exact=True)).to_be_visible()


def test_browser_source_previews_skip_examples_without_thumbnails(db, browser_console):
    from playwright.sync_api import expect
    page, origin = browser_console
    service = SubjectManagement(db)
    subject = enroll(db, 8)[0]
    source = service.sources(subject)['sources'][0]['source_id']
    first = service.examples(subject, source_id=source, limit=3)['examples']
    sql(db, 'UPDATE subject_representative_face SET active=false,retired_at=now() WHERE example_id=ANY(%s::uuid[])',
        ([e['example_id'] for e in first],))
    assert all(e['preview_url'] is None for e in service.examples(subject, source_id=source, limit=3)['examples'])
    previews = service.examples(subject, source_id=source, limit=3, with_previews=True)
    assert previews['examples']
    span = sql(db, 'SELECT min(start_ms),max(end_ms) FROM subject_example WHERE subject_id=%s AND source_id=%s', (subject, source))[0]
    assert previews['source_time_range'] == {'start_ms': span[0], 'end_ms': span[1]}
    assert all(e['preview_url'] and e['source_id'] == source for e in previews['examples'])
    single = service.examples(subject, source_id=source, limit=1, with_previews=True)
    if single['next_cursor']:
        following = service.examples(subject, source_id=source, limit=1, after=single['next_cursor'], with_previews=True)
        assert following['examples'][0]['example_id'] != single['examples'][0]['example_id']
    page.goto(origin + '/sources/' + source)
    images = page.locator('#subject-detail .subject-card .representatives img')
    expect(images).to_have_count(len(previews['examples']))
    expect(page.locator('.source-time-range')).to_have_text(f'{span[0]/1000:.1f}–{span[1]/1000:.1f} seconds')
    expect(page.get_by_text('No preview available for this source.', exact=True)).to_have_count(0)
    invalid = page.request.get(origin + f'/api/subjects/{subject}/examples?with_previews=invalid')
    assert invalid.status == 422
    sql(db, 'UPDATE subject_representative_face r SET active=false,retired_at=now() FROM subject_example e WHERE r.example_id=e.example_id AND e.subject_id=%s', (subject,))
    assert service.examples(subject, source_id=source, with_previews=True)['examples'] == []
    assert len(service.examples(subject, source_id=source)['examples']) == 8
    page.reload()
    expect(page.get_by_text('No preview available for this source.', exact=True)).to_be_visible()


def test_browser_compact_run_grid_and_direct_merge_stale_selection(db, browser_console):
    from playwright.sync_api import expect
    page, origin = browser_console
    run, groups = make_run(db, count=2)
    RunRepository(db).select(run, groups, 1)
    worker, _ = processor(db)
    assert worker.match(run)
    page.goto(origin + '/runs/' + run)
    cards = page.locator('.enrolled-subject')
    expect(cards).to_have_count(2)
    boxes = [card.bounding_box() for card in cards.all()]
    assert abs(boxes[0]['y'] - boxes[1]['y']) < 2
    assert boxes[1]['x'] > boxes[0]['x'] + boxes[0]['width']
    if os.getenv('FACE_BROWSER_SCREENSHOTS'):
        folder = Path(os.environ['FACE_BROWSER_SCREENSHOTS'])
        folder.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(folder / 'compact-run-desktop.png'))
        page.set_viewport_size({'width': 390, 'height': 844})
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        page.screenshot(path=str(folder / 'compact-run-mobile.png'))
    service = SubjectManagement(db)
    subjects = [s['subject_id'] for s in service.subjects()['subjects']]
    source = service.sources(subjects[0])['sources'][0]['source_id']
    page.goto(origin + '/sources/' + source)
    page.get_by_role('button', name='Select all', exact=True).click()
    expect(page.get_by_label('Keep subject', exact=True)).to_be_visible()
    sql(db, 'UPDATE subject SET row_version=row_version+1 WHERE subject_id=%s', (subjects[0],))
    page.get_by_role('button', name='Merge', exact=True).click()
    expect(page.get_by_role('alert')).to_be_visible()
    expect(page.get_by_role('dialog')).to_have_count(0)
    assert len(service.subjects(source_id=source)['subjects']) == 2


def source_merge_fixture(db, angles):
    run, groups = make_run(db, len(angles))
    for group, angle in zip(groups, angles, strict=True):
        sql(db, 'UPDATE submission_face_group SET aggregate_embedding=%s::vector WHERE group_id=%s', (pgvector(vector(angle)), group))
    RunRepository(db).select(run, groups, 1)
    worker, _ = processor(db)
    assert worker.match(run)
    ids = [str(r[0]) for r in sql(db, 'SELECT subject_id FROM subject_example JOIN submission_face_group ON submission_group_id=group_id WHERE run_id=%s ORDER BY local_group_id', (run,))]
    source = SubjectManagement(db).sources(ids[0])['sources'][0]['source_id']
    return source, ids


def test_source_groups_scope_replay_partial_resume_and_recovery(db):
    from worker.source_merges import SourceMerges
    service = SubjectManagement(db)
    planner = SourceMerges(service, apply_enabled=True)
    source, ids = source_merge_fixture(db, [0, .05, 1.5, 1.55, 3])
    lookalike = enroll(db)[0]
    origins = sql(db, 'SELECT example_id,source_id FROM subject_example ORDER BY example_id')
    plan = planner.plan(source, {'threshold': .99})
    assert len(plan['groups']) == 2
    assert {frozenset(m['subject_id'] for m in g['members']) for g in plan['groups']} == {frozenset(ids[:2]), frozenset(ids[2:4])}
    assert lookalike not in str(plan)
    for group in plan['groups']:
        group['selected'] = True
    stale_id = plan['groups'][1]['members'][0]['subject_id']
    sql(db, 'UPDATE subject SET row_version=row_version+1 WHERE subject_id=%s', (stale_id,))
    assert [o['status'] for o in planner.apply(source, plan, 'tester')['outcomes']] == ['merged', 'stale']
    assert planner.apply(source, plan, 'tester')['outcomes'][0]['status'] == 'merged'
    assert len(sql(db, 'SELECT * FROM subject_change_event WHERE operation_id=ANY(%s::uuid[])', ([g['operation_id'] for g in plan['groups']],))) == 2
    assert len(sql(db, "SELECT * FROM subject_change_event WHERE action='combine' AND result ? 'destination'")) == 1
    refreshed = planner.plan(source, {'threshold': .99})
    assert len(refreshed['groups']) == 1
    refreshed['groups'][0]['selected'] = True
    assert planner.apply(source, refreshed, 'tester')['outcomes'][0]['status'] == 'merged'
    assert service.subject(lookalike)['sample_count'] == service.subject(ids[-1])['sample_count'] == 1
    assert sql(db, 'SELECT example_id,source_id FROM subject_example ORDER BY example_id') == origins
    first = plan['groups'][0]
    survivor = first['survivor_id']
    history = service.merge_members(survivor)
    assert history['members'][0]['can_separate']
    service.separate_merge(survivor, {'operation_id': str(uuid.uuid4()), 'version': history['version'],
        'merge_operation_id': first['operation_id'], 'member_subject_id': history['members'][0]['subject_id']}, 'tester')
    assert sql(db, 'SELECT example_id,source_id FROM subject_example ORDER BY example_id') == origins
    event = sql(db, 'SELECT details FROM subject_change_event WHERE operation_id=%s', (first['operation_id'],))[0][0]
    assert event['source_merge']['algorithm'] == 'complete-linkage-v1'
    assert event['source_merge']['threshold'] == .99


def test_source_boundary_rechecked_and_multisource_skipped(db):
    from worker.source_merges import SourceMerges
    service = SubjectManagement(db)
    planner = SourceMerges(service, apply_enabled=True)
    source, ids = source_merge_fixture(db, [0, .01, .02])
    other = enroll(db)[0]
    plan = planner.plan(source, {'threshold': .99})
    plan['groups'][0]['selected'] = True
    example = service.examples(other)['examples'][0]
    service.move(other, {'operation_id': str(uuid.uuid4()), 'version': service.subject(other)['version'],
        'example_ids': [example['example_id']], 'target_subject_id': ids[1], 'target_version': service.subject(ids[1])['version']}, 'tester')
    before = sql(db, 'SELECT subject_id,example_id FROM subject_example ORDER BY example_id')
    outcome = planner.apply(source, plan, 'tester')['outcomes'][0]
    assert outcome['status'] == 'stale' and outcome['code'] == 'source_membership_changed'
    assert sql(db, 'SELECT subject_id,example_id FROM subject_example ORDER BY example_id') == before
    new_plan = planner.plan(source, {'threshold': .99})
    assert new_plan['skipped'] == [{'subject_id': ids[1], 'reason': 'multi_source'}]


def test_source_chain_dismissals_identity_and_forged_score(db):
    from worker.source_merges import SourceMerges
    service = SubjectManagement(db)
    planner = SourceMerges(service, apply_enabled=True)
    source, ids = source_merge_fixture(db, [0, .3, .6])
    plan = planner.plan(source, {'threshold': .94})
    assert len(plan['groups']) == 1 and len(plan['groups'][0]['members']) == 2
    group = plan['groups'][0]
    a, b = group['members']
    service.suggestion_dismissal(a['subject_id'], b['subject_id'], {'operation_id': str(uuid.uuid4()), 'version': a['version'], 'target_version': b['version']}, 'tester')
    group['selected'] = True
    assert planner.apply(source, plan, 'tester')['outcomes'][0]['code'] == 'pair_ineligible'
    assert sorted([a['subject_id'], b['subject_id']]) in planner.plan(source, {'threshold': .94})['dismissed_pairs']
    source2, ids2 = source_merge_fixture(db, [0, .01])
    for sid, label in zip(ids2, ['Alice', 'Bob'], strict=True):
        service.edit(sid, {'operation_id': str(uuid.uuid4()), 'version': service.subject(sid)['version'], 'identity_version': None, 'display_name': label, 'external_identity_ref': None}, 'tester')
    plan2 = planner.plan(source2, {'threshold': .99})
    group2 = plan2['groups'][0]
    assert group2['identity_conflicts'] == ['display_name']
    group2['selected'] = True
    assert planner.apply(source2, plan2, 'tester')['outcomes'][0]['code'] == 'identity_conflict'
    group2['review_identity_conflicts'] = True
    assert planner.apply(source2, plan2, 'tester')['outcomes'][0]['code'] == 'operation_conflict'
    plan2 = planner.plan(source2, {'threshold': .99})
    group2 = plan2['groups'][0]
    group2['selected'] = group2['review_identity_conflicts'] = True
    assert planner.apply(source2, plan2, 'tester')['outcomes'][0]['status'] == 'merged'
    assert planner.apply(source2, {**plan2, 'threshold': .98}, 'tester')['outcomes'][0]['code'] == 'operation_conflict'
    forged = planner.plan(source, {'threshold': .94})
    forged['groups'][0]['members'] = [service.subject(sid) for sid in ids]
    forged['groups'][0]['minimum_similarity'] = 1
    forged['groups'][0]['selected'] = True
    assert planner.apply(source, forged, 'tester')['outcomes'][0]['code'] == 'pair_ineligible'


def test_source_missing_models_limits_and_read_only_rollout(db, monkeypatch):
    from worker import source_merges
    service = SubjectManagement(db)
    planner = source_merges.SourceMerges(service)
    source, ids = source_merge_fixture(db, [0, .01, .02, .03])
    sql(db, 'UPDATE subject SET canonical_embedding=NULL WHERE subject_id=%s', (ids[0],))
    sql(db, "UPDATE subject SET model_version='other' WHERE subject_id=%s", (ids[1],))
    plan = planner.plan(source, {'threshold': .99})
    assert {m['reason'] for m in plan['skipped']} == {'missing_embedding', 'model_mismatch'}
    assert len(plan['groups']) == 1 and {m['subject_id'] for m in plan['groups'][0]['members']} == set(ids[2:])
    with pytest.raises(SubjectError) as error:
        planner.apply(source, plan, 'tester')
    assert error.value.code == 'proposals_only'
    monkeypatch.setattr(source_merges, 'MAX_SUBJECTS', 3)
    with pytest.raises(SubjectError) as error:
        planner.plan(source, {'threshold': .99})
    assert error.value.code == 'background_scan_required'


def test_browser_source_matching_mobile_and_cli_parity(db, browser_console, monkeypatch, tmp_path):
    from playwright.sync_api import expect

    from worker.source_merges_cli import apply_plan, save_private
    monkeypatch.setenv('FACE_SOURCE_MERGES_APPLY_ENABLED', 'true')
    page, origin = browser_console
    source, _ids = source_merge_fixture(db, [0, .01, 1.5, 1.51])
    page.set_viewport_size({'width': 390, 'height': 844})
    page.goto(origin + '/sources/' + source)
    expect(page.locator('.source-merge-panel')).not_to_have_attribute('open', '')
    page.locator('.source-merge-panel > summary').click()
    page.get_by_label('Reviewed cosine threshold').fill('0.99')
    page.get_by_role('button', name='Find matching groups', exact=True).click()
    expect(page.locator('.merge-group')).to_have_count(2)
    expect(page.locator('.merge-group h3')).to_have_text(['Group 1', 'Group 2'])
    expect(page.locator('.merge-correction[open]')).to_have_count(0)
    expect(page.get_by_role('button', name='Merge selected groups', exact=True)).to_be_disabled()
    page.get_by_role('button', name='Select all groups', exact=True).click()
    expect(page.locator('.merge-selection-summary')).to_have_text('2 groups selected · 4 subjects → 2 subjects')
    # A saved proposal-only review must use the current server gate, preserving selections.
    page.evaluate('''() => {
        const saved = JSON.parse(sessionStorage.getItem('source-merge-review-v1'));
        for (const plan of Object.values(saved)) plan.apply_enabled = false;
        sessionStorage.setItem('source-merge-review-v1', JSON.stringify(saved));
    }''')
    page.reload()
    expect(page.locator('.source-merge-panel')).not_to_have_attribute('open', '')
    page.locator('.source-merge-panel > summary').click()
    expect(page.locator('.merge-selection-summary')).to_have_text('2 groups selected · 4 subjects → 2 subjects')
    expect(page.get_by_role('button', name='Merge selected groups', exact=True)).to_be_enabled()
    monkeypatch.setenv('FACE_SOURCE_MERGES_APPLY_ENABLED', 'false')
    page.reload()
    expect(page.locator('.source-merge-panel')).not_to_have_attribute('open', '')
    page.locator('.source-merge-panel > summary').click()
    expect(page.get_by_text('Proposal review only. Operator application is not enabled.', exact=True)).to_be_visible()
    expect(page.get_by_role('button', name='Merge selected groups', exact=True)).to_be_disabled()
    monkeypatch.setenv('FACE_SOURCE_MERGES_APPLY_ENABLED', 'true')
    page.reload()
    expect(page.locator('.source-merge-panel')).not_to_have_attribute('open', '')
    page.locator('.source-merge-panel > summary').click()
    expect(page.get_by_role('button', name='Merge selected groups', exact=True)).to_be_enabled()
    page.get_by_role('button', name='Clear group selection', exact=True).click()
    expect(page.get_by_role('button', name='Merge selected groups', exact=True)).to_be_disabled()
    expect(page.locator('.merge-survivor-badge')).to_have_count(2)
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    page.locator('.merge-group').first.get_by_label('Include group', exact=True).check()
    page.get_by_role('button', name='Merge selected groups', exact=True).click()
    expect(page.locator('.merge-outcome')).to_contain_text(['merged:'])
    expect(page.locator('.source-merge-panel')).not_to_have_attribute('open', '')
    expect(page.locator('.source-merge-panel > summary')).to_have_text('Matching groups · 1 group merged')
    expect(page.get_by_role('dialog')).to_have_count(0)
    page.reload()
    expect(page.locator('.source-merge-panel')).not_to_have_attribute('open', '')
    page.locator('.source-merge-panel > summary').click()
    expect(page.locator('.merge-outcome')).to_contain_text(['merged:'])
    class Client:
        def post(self, path, data):
            response = page.request.post(origin + path, data=data, headers={'Origin': origin})
            assert response.ok, response.text()
            return response.json()
    client = Client()
    client.origin = origin
    plan = client.post(f'/api/sources/{source}/merge-proposals', {'threshold': .99})
    plan['groups'][0]['selected'] = True
    path, receipt = tmp_path / 'plan.json', tmp_path / 'receipt.json'
    save_private(path, {'format_version': 1, 'origin': origin, 'plans': [plan]})
    result = apply_plan(client, path, receipt)
    assert next(iter(result['operations'].values()))['outcome']['status'] == 'merged'
    assert apply_plan(client, path, receipt) == result
    assert len(SubjectManagement(db).subjects(source_id=source)['subjects']) == 2
    assert path.stat().st_mode & 0o777 == 0o600


def test_source_oversized_group_never_split_or_applied(db):
    from worker.source_merges import SourceMerges
    service = SubjectManagement(db)
    planner = SourceMerges(service, apply_enabled=True)
    source, ids = source_merge_fixture(db, [0] * 51)
    plan = planner.plan(source, {'threshold': .99})
    assert len(plan['groups']) == 1
    assert len(plan['groups'][0]['members']) == 51
    assert plan['groups'][0]['manual_handling_required']
    plan['groups'][0]['selected'] = True
    outcome = planner.apply(source, plan, 'tester')['outcomes'][0]
    assert outcome['status'] == 'failed' and outcome['code'] == 'manual_handling_required'
    assert len(service.subjects(source_id=source, limit=100)['subjects']) == len(ids)


def test_source_recheck_waits_for_enrollment_lock_then_rejects_entire_group(db):
    import concurrent.futures
    import threading

    from worker.source_merges import SourceMerges
    service = SubjectManagement(db)
    planner = SourceMerges(service, apply_enabled=True)
    source, ids = source_merge_fixture(db, [0, .01, .02])
    plan = planner.plan(source, {'threshold': .99})
    plan['groups'][0]['selected'] = True
    started = threading.Event()
    def apply():
        started.set()
        return planner.apply(source, plan, 'tester')
    connection = db.connect()
    try:
        cursor = connection.cursor()
        cursor.execute('SELECT pg_advisory_xact_lock(8675309)')
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(apply)
            assert started.wait(5)
            cursor.execute('UPDATE subject SET row_version=row_version+1 WHERE subject_id=%s', (ids[1],))
            connection.commit()
            outcome = future.result(timeout=10)['outcomes'][0]
            assert outcome['status'] == 'stale' and outcome['code'] == 'stale_subject'
    finally:
        connection.close()
    assert len(service.subjects(source_id=source)['subjects']) == 3


def test_browser_multiple_source_matching_is_independent(db, browser_console):
    from playwright.sync_api import expect
    page, origin = browser_console
    for _ in range(2):
        source_merge_fixture(db, [0, .01])
    page.goto(origin + '/sources')
    expect(page.get_by_label('Select source for matching')).to_have_count(2)
    for checkbox in page.get_by_label('Select source for matching').all():
        checkbox.check()
    expect(page.locator('.source-merge-panel')).not_to_have_attribute('open', '')
    page.locator('.source-merge-panel > summary').click()
    page.get_by_label('Reviewed cosine threshold').fill('0.99')
    page.get_by_role('button', name='Find matching groups', exact=True).click()
    expect(page.locator('.source-merge-result')).to_have_count(2)
    expect(page.locator('.merge-group')).to_have_count(2)
    expect(page.get_by_text('Proposal review only. Operator application is not enabled.', exact=True)).to_be_visible()
    page.set_viewport_size({'width': 390, 'height': 844})
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')


def test_browser_source_group_exclusion_and_dismissal(db, browser_console, monkeypatch):
    from playwright.sync_api import expect
    monkeypatch.setenv('FACE_SOURCE_MERGES_APPLY_ENABLED', 'true')
    page, origin = browser_console
    source, _ids = source_merge_fixture(db, [0, .01, .02])
    page.goto(origin + '/sources/' + source)
    expect(page.locator('.source-merge-panel')).not_to_have_attribute('open', '')
    page.locator('.source-merge-panel > summary').click()
    page.get_by_label('Reviewed cosine threshold').fill('0.99')
    page.get_by_role('button', name='Find matching groups', exact=True).click()
    expect(page.locator('.merge-group-member')).to_have_count(3)
    page.get_by_role('button', name='Exclude member', exact=True).first.click()
    expect(page.locator('.merge-group-member')).to_have_count(2)
    expect(page.get_by_text('Minimum similarity lower bound after exclusion', exact=False)).to_be_visible()
    page.get_by_text('Fix a mismatch', exact=True).click()
    page.get_by_role('button', name='These are different people', exact=True).click()
    expect(page.locator('.merge-outcome')).to_contain_text('Pair dismissed')
    page.get_by_role('button', name='Find matching groups', exact=True).click()
    expect(page.locator('.source-merge-result')).to_contain_text('1 currently dismissed pairs excluded')
    page.locator('.merge-group').get_by_label('Include group', exact=True).check()
    page.get_by_role('button', name='Merge selected groups', exact=True).click()
    expect(page.locator('.merge-outcome')).to_contain_text('merged:')
    assert len(SubjectManagement(db).subjects(source_id=source)['subjects']) == 2


def test_source_identity_changes_and_concurrent_manual_merge_are_stale(db):
    from worker.source_merges import SourceMerges
    service = SubjectManagement(db)
    planner = SourceMerges(service, apply_enabled=True)
    source, ids = source_merge_fixture(db, [0, .01, .02])
    service.edit(ids[0], {'operation_id': str(uuid.uuid4()), 'version': service.subject(ids[0])['version'],
        'identity_version': None, 'display_name': 'Reviewed', 'external_identity_ref': None}, 'tester')
    plan = planner.plan(source, {'threshold': .99})
    plan['groups'][0]['selected'] = True
    identity = service.subject(ids[0])['identity_id']
    sql(db, "UPDATE identity SET display_name='Changed',row_version=row_version+1 WHERE identity_id=%s", (identity,))
    assert planner.apply(source, plan, 'tester')['outcomes'][0]['code'] == 'identity_changed'
    plan = planner.plan(source, {'threshold': .99})
    plan['groups'][0]['selected'] = True
    service.bulk_combine(ids[0], {'operation_id': str(uuid.uuid4()), 'version': service.subject(ids[0])['version'],
        'subjects': [{'subject_id': ids[1], 'version': service.subject(ids[1])['version']}]}, 'tester')
    before = sql(db, 'SELECT subject_id,example_id FROM subject_example ORDER BY example_id')
    assert planner.apply(source, plan, 'tester')['outcomes'][0]['code'] == 'source_membership_changed'
    assert sql(db, 'SELECT subject_id,example_id FROM subject_example ORDER BY example_id') == before


def test_source_merge_rolls_back_mutation_and_persists_definitive_failure(db):
    from worker.source_merges import SourceMerges
    service = SubjectManagement(db)
    planner = SourceMerges(service, apply_enabled=True)
    source, ids = source_merge_fixture(db, [0, math.pi])
    plan = planner.plan(source, {'threshold': -1})
    group = plan['groups'][0]
    group['selected'] = True
    before = sql(db, 'SELECT subject_id,example_id FROM subject_example ORDER BY example_id')
    result = planner.apply(source, plan, 'tester')
    assert result['outcomes'][0]['code'] == 'invalid_embedding'
    assert sql(db, 'SELECT subject_id,example_id FROM subject_example ORDER BY example_id') == before
    assert all(service.subject(sid)['subject_id'] == sid for sid in ids)
    assert planner.apply(source, plan, 'tester') == result
    assert len(sql(db, 'SELECT * FROM subject_change_event WHERE operation_id=%s', (group['operation_id'],))) == 1
    assert service.merge_members(group['survivor_id'])['members'] == []
