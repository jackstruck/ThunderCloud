import uuid
from datetime import UTC, datetime, timedelta

from worker.console import create_app
from worker.run_repository import CreatedRun, RunConflictError, RunNotFoundError
from worker.uploads import UploadedObject

ORIGIN = "https://faces.example.test"
AUTH = {
    "Origin": ORIGIN,
    "X-Goog-Authenticated-User-Email": "accounts.google.com:member@example.test",
}
RUN_ID = str(uuid.uuid4())
GROUP_ID = str(uuid.uuid4())
REPRESENTATIVE_ID = str(uuid.uuid4())
SUBJECT_ID = str(uuid.uuid4())


def run_record(state="fetching"):
    now = datetime.now(UTC).isoformat()
    return {
        "run_id": RUN_ID,
        "state": state,
        "handling_policy": "search_then_discard",
        "status_url": f"/runs/{RUN_ID}",
        "outcome": None,
        "retryable": False,
        "created_at": now,
        "updated_at": now,
        "expires_at": (datetime.now(UTC) + timedelta(days=7)).isoformat(),
    }


class Repository:
    def __init__(self):
        self.create_calls = []
        self.record = run_record()

    def create(self, principal, key, payload):
        self.create_calls.append((principal, key, payload))
        return CreatedRun(self.record, True)

    def get(self, run_id):
        if run_id != RUN_ID:
            raise RunNotFoundError(run_id)
        return self.record

    def recent(self, principal):
        assert principal == "member@example.test"
        return [self.record]

    def groups(self, run_id):
        return (
            [
                {
                    "group_id": GROUP_ID,
                    "preview_url": "/preview",
                    "selected": False,
                    "quality": {},
                    "time_range_ms": None,
                }
            ],
            4,
        )

    def select(self, run_id, group_ids, version):
        assert group_ids == [GROUP_ID]
        assert version == 4
        return run_record("matching")

    def results(self, run_id):
        return {"run_id": run_id, "groups": []}

    def retry(self, run_id):
        return run_record("fetching")

    def cancel(self, run_id):
        return run_record("cancelled")

    def finalize_upload(self, run_id, **values):
        assert values == {
            "expected_bytes": 123,
            "expected_sha256": "a" * 64,
            "object_generation": 7,
            "object_bytes": 123,
        }
        return run_record("queued")


class Uploads:
    def create_session(self, run_id):
        assert run_id == RUN_ID
        return "https://storage.example.test/resumable-secret"

    def verify_completed(self, run_id):
        assert run_id == RUN_ID
        return UploadedObject(7, 123)


class Gallery:
    def preview(self, run_id, group_id):
        assert (run_id, group_id) == (RUN_ID, GROUP_ID)
        return b"preview"

    def representative(self, representative_id):
        assert representative_id == REPRESENTATIVE_ID
        return b"representative"

    def subject(self, subject_id):
        assert subject_id == SUBJECT_ID
        return {"subject_id": subject_id, "representative_faces": []}


def client(
    repository=None,
    invoke=None,
    arbitrary=False,
    uploads=None,
    invoke_detect=None,
    invoke_match=None,
    gallery=None,
    enrollment=False,
):
    app = create_app(
        repository or Repository(),
        allowed_origin=ORIGIN,
        invoke_ingest=invoke,
        invoke_gpu_detect=invoke_detect,
        invoke_gpu_match=invoke_match,
        allow_arbitrary_hosts=arbitrary,
        upload_service=uploads,
        gallery_service=gallery,
        enrollment_enabled=enrollment,
    )
    app.config["TESTING"] = True
    return app.test_client()


def post_run(http, source, policy="search_then_discard", headers=None):
    request_headers = {**AUTH, "Idempotency-Key": "0123456789abcdef"}
    request_headers.update(headers or {})
    return http.post(
        "/api/runs",
        headers=request_headers,
        json={"handling_policy": policy, "source": source},
    )


def test_iap_identity_is_required_for_every_endpoint():
    response = client().get(f"/api/runs/{RUN_ID}")
    assert response.status_code == 401
    assert response.json["code"] == "authentication_required"


def test_recent_runs_are_scoped_to_authenticated_principal():
    response = client().get("/api/runs/recent", headers=AUTH)
    assert response.status_code == 200
    assert len(response.json["runs"]) == 1
    assert response.json["runs"][0]["run_id"] == RUN_ID


def test_mutation_rejects_wrong_origin():
    response = post_run(
        client(),
        {"kind": "upload", "content_type": "image/jpeg", "bytes": 10},
        headers={"Origin": "https://attacker.test"},
    )
    assert response.status_code == 403
    assert response.json["code"] == "origin_rejected"


def test_phase1_rejects_retention_with_stable_error():
    response = post_run(
        client(),
        {"kind": "upload", "content_type": "image/jpeg", "bytes": 10},
        policy="retain_and_enroll",
    )
    assert response.status_code == 422
    assert response.json == {
        "code": "feature_not_available",
        "message": "Enrollment is not available in Phase 1.",
    }


def test_phase2_accepts_explicit_retention_policy():
    repository = Repository()
    response = post_run(
        client(repository, enrollment=True),
        {"kind": "upload", "content_type": "image/jpeg", "bytes": 10},
        policy="retain_and_enroll",
    )
    assert response.status_code == 202
    assert repository.create_calls[0][2]["handling_policy"] == "retain_and_enroll"


def test_phase2_accepts_enroll_only_policy():
    repository = Repository()
    response = post_run(
        client(repository, enrollment=True),
        {"kind": "upload", "content_type": "image/jpeg", "bytes": 10},
        policy="enroll_only",
    )
    assert response.status_code == 202
    assert repository.create_calls[0][2]["handling_policy"] == "enroll_only"


def test_upload_run_returns_immediately_and_records_verified_principal():
    repository = Repository()
    response = post_run(
        client(repository),
        {"kind": "upload", "content_type": "image/png", "bytes": 123},
    )
    assert response.status_code == 202
    assert response.json["run_id"] == RUN_ID
    assert response.headers["Location"] == f"/runs/{RUN_ID}"
    assert response.headers["Cache-Control"] == "no-store"
    assert repository.create_calls[0][0] == "member@example.test"


def test_url_adapter_run_invokes_ingestion_once_after_creation():
    invoked = []
    response = post_run(
        client(invoke=invoked.append),
        {"kind": "url", "url": "https://justpaste.it/example"},
    )
    assert response.status_code == 202
    assert invoked == [RUN_ID]


def test_arbitrary_host_is_disabled_until_ssrf_gate_passes():
    response = post_run(
        client(), {"kind": "url", "url": "https://media.example.test/a.mp4"}
    )
    assert response.status_code == 422
    assert response.json["code"] == "source_not_allowlisted"


def test_heylink_is_always_rejected():
    response = post_run(
        client(arbitrary=True), {"kind": "url", "url": "https://heylink.me/a"}
    )
    assert response.status_code == 422
    assert response.json["code"] == "unsupported_source"


def test_face_groups_return_selection_etag_and_no_store():
    response = client().get(f"/api/runs/{RUN_ID}/face-groups", headers=AUTH)
    assert response.status_code == 200
    assert response.headers["ETag"] == '"4"'
    assert response.headers["Cache-Control"] == "no-store"


def test_private_preview_and_gallery_are_authenticated_no_store_jpegs():
    http = client(gallery=Gallery())
    preview = http.get(
        f"/api/runs/{RUN_ID}/face-groups/{GROUP_ID}/preview", headers=AUTH
    )
    representative = http.get(f"/api/gallery/faces/{REPRESENTATIVE_ID}", headers=AUTH)
    assert preview.data == b"preview"
    assert representative.data == b"representative"
    for response in (preview, representative):
        assert response.content_type == "image/jpeg"
        assert response.headers["Cache-Control"] == "no-store"


def test_subject_page_returns_representative_metadata():
    response = client(gallery=Gallery()).get(
        f"/api/subjects/{SUBJECT_ID}", headers=AUTH
    )
    assert response.status_code == 200
    assert response.json["subject_id"] == SUBJECT_ID


def test_upload_session_returns_secret_uri_without_cache_or_referrer():
    response = client(uploads=Uploads()).post(
        f"/api/runs/{RUN_ID}/upload-session", headers=AUTH
    )
    assert response.status_code == 201
    assert response.json["session_uri"].startswith("https://storage.example.test/")
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Referrer-Policy"] == "no-referrer"


def test_upload_completion_records_generation_then_invokes_ingestion():
    invoked = []
    response = client(uploads=Uploads(), invoke_detect=invoked.append).post(
        f"/api/runs/{RUN_ID}/upload-complete",
        headers=AUTH,
        json={"bytes": 123, "sha256": "a" * 64},
    )
    assert response.status_code == 202
    assert response.json["state"] == "queued"
    assert invoked == [RUN_ID]


def test_selection_requires_matching_etag_shape():
    invoked = []
    response = client(invoke_match=invoked.append).put(
        f"/api/runs/{RUN_ID}/face-selection",
        headers={**AUTH, "If-Match": '"4"'},
        json={"group_ids": [GROUP_ID]},
    )
    assert response.status_code == 202
    assert response.json["state"] == "matching"
    assert invoked == [RUN_ID]


def test_unknown_run_returns_same_safe_404():
    response = client().get(f"/api/runs/{uuid.uuid4()}", headers=AUTH)
    assert response.status_code == 404
    assert response.json == {"code": "run_not_found", "message": "Run not found."}


def test_repository_conflict_is_sanitized():
    repository = Repository()
    repository.create = lambda *_args: (_ for _ in ()).throw(
        RunConflictError("active run quota reached")
    )
    response = post_run(
        client(repository),
        {"kind": "upload", "content_type": "image/jpeg", "bytes": 10},
    )
    assert response.status_code == 409
    assert response.json["code"] == "run_conflict"


def test_hotscope_video_is_accepted_without_arbitrary_host_access():
    invoked = []
    response = post_run(client(invoke=invoked.append),
                        {'kind': 'url', 'url': 'https://hotscope.tv/video/abc'})
    assert response.status_code == 202
    assert invoked == [RUN_ID]


def test_hotscope_profile_is_rejected_as_a_single_video_source():
    response = post_run(client(), {'kind': 'url', 'url': 'https://hotscope.tv/user/alice'})
    assert response.status_code == 422
    assert response.json['code'] == 'invalid_url'
