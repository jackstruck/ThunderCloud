from __future__ import annotations

import hmac
import os
import re
import uuid
from typing import Any
from urllib.parse import urlsplit

from flask import Flask, Response, g, jsonify, request, send_from_directory

from .config import Settings
from .db import Database
from .gallery import GalleryRepository, GalleryService
from .job_invoker import CloudRunJobInvoker
from .rate_limit import FixedWindowRateLimiter
from .run_repository import RunConflictError, RunNotFoundError, RunRepository
from .storage import StorageRepository, load_configured_csek, validate_sha256
from .uploads import UploadService

ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "video/mp4"}
ADAPTER_HOST_SUFFIXES = {
    "justpaste.it": "justpaste",
    "luluvid.com": "luluvid",
    "www.luluvid.com": "luluvid",
}
IDEMPOTENCY_RE = re.compile(r"^[\x21-\x7e]{16,128}$")


class RequestError(ValueError):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _error(status: int, code: str, message: str):
    return jsonify({"code": code, "message": message}), status


def _principal() -> str:
    value = request.headers.get("X-Goog-Authenticated-User-Email", "")
    if value.startswith("accounts.google.com:"):
        value = value.removeprefix("accounts.google.com:")
    if not value or "@" not in value or any(char.isspace() for char in value):
        raise RequestError(
            401, "authentication_required", "IAP authentication is required."
        )
    return value.lower()


def _json_object() -> dict[str, Any]:
    if not request.is_json:
        raise RequestError(415, "unsupported_media_type", "Expected application/json.")
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        raise RequestError(
            400, "invalid_request", "Request body must be a JSON object."
        )
    return value


def _create_payload(
    value: dict[str, Any], allow_arbitrary_hosts: bool, enrollment_enabled: bool
) -> dict[str, Any]:
    if set(value) != {"handling_policy", "source"}:
        raise RequestError(
            422, "invalid_request", "Unexpected or missing request fields."
        )
    policy = value["handling_policy"]
    if policy == "retain_and_enroll" and not enrollment_enabled:
        raise RequestError(
            422,
            "feature_not_available",
            "Retained enrollment is not available in Phase 1.",
        )
    if policy not in {"search_then_discard", "retain_and_enroll"}:
        raise RequestError(
            422, "invalid_handling_policy", "Unsupported handling policy."
        )
    source = value["source"]
    if not isinstance(source, dict) or "kind" not in source:
        raise RequestError(422, "invalid_source", "A source object is required.")
    if source["kind"] == "upload":
        if set(source) != {"kind", "content_type", "bytes"}:
            raise RequestError(422, "invalid_source", "Invalid upload source fields.")
        if source["content_type"] not in ALLOWED_CONTENT_TYPES:
            raise RequestError(422, "unsupported_content", "Use JPEG, PNG, or MP4.")
        if type(source["bytes"]) is not int or source["bytes"] <= 0:
            raise RequestError(
                422, "invalid_source", "Upload byte length must be positive."
            )
    elif source["kind"] == "url":
        if set(source) != {"kind", "url"} or not isinstance(source["url"], str):
            raise RequestError(422, "invalid_source", "Invalid URL source fields.")
        parsed = urlsplit(source["url"])
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
        ):
            raise RequestError(
                422, "invalid_url", "Use an HTTPS URL without credentials or fragments."
            )
        host = parsed.hostname.rstrip(".").lower()
        if host == "heylink.me" or host.endswith(".heylink.me"):
            raise RequestError(422, "unsupported_source", "HeyLink is not supported.")
        if host not in ADAPTER_HOST_SUFFIXES and not allow_arbitrary_hosts:
            raise RequestError(
                422,
                "source_not_allowlisted",
                "Direct-host fetching is disabled until the SSRF transport gate passes.",
            )
    else:
        raise RequestError(422, "invalid_source", "Source kind must be url or upload.")
    return value


def _etag_version(value: str | None) -> int:
    if not value:
        raise RequestError(428, "etag_required", "If-Match is required.")
    normalized = value.removeprefix("W/").strip('"')
    try:
        version = int(normalized)
    except ValueError as error:
        raise RequestError(
            400, "invalid_etag", "If-Match must contain the selection version."
        ) from error
    if version < 1:
        raise RequestError(400, "invalid_etag", "Selection version must be positive.")
    return version


def create_app(
    repository=None,
    *,
    allowed_origin: str | None = None,
    allow_arbitrary_hosts: bool | None = None,
    enrollment_enabled: bool | None = None,
    invoke_ingest=None,
    invoke_gpu_detect=None,
    invoke_gpu_match=None,
    invoke_maintenance=None,
    upload_service=None,
    gallery_service=None,
    rate_limiter=None,
) -> Flask:
    app = Flask(__name__, static_folder=None)
    configured_repository = repository is None
    if repository is None:
        settings = Settings.from_env()
        database = Database(
            settings.cloud_sql_instance,
            settings.db_user,
            settings.db_name,
            settings.cloud_sql_ip_type,
        )
        repository = RunRepository(
            database, int(os.getenv("FACE_ACTIVE_RUN_LIMIT", "3"))
        )
        csek = load_configured_csek(
            settings.project_id, settings.csek_file, settings.csek_secret
        )
        storage = StorageRepository(
            settings.project_id,
            settings.bucket,
            settings.source_prefix,
            settings.staging_prefix,
            csek,
        )
    origin = allowed_origin or os.getenv("FACE_CONSOLE_ORIGIN")
    if allow_arbitrary_hosts is None:
        allow_arbitrary_hosts = os.getenv(
            "FACE_ALLOW_ARBITRARY_HOSTS", "false"
        ).lower() in {"1", "true", "yes"}
    if enrollment_enabled is None:
        enrollment_enabled = os.getenv(
            "FACE_RETAINED_ENROLLMENT_ENABLED", "false"
        ).lower() in {"1", "true", "yes"}
    rate_limiter = rate_limiter or FixedWindowRateLimiter()
    if upload_service is None and repository is not None and "storage" in locals():
        upload_service = UploadService(storage, repository, origin or "")
    if gallery_service is None and configured_repository:
        gallery_service = GalleryService(GalleryRepository(database), storage)
    if invoke_ingest is None:
        if configured_repository:
            invoke_ingest = CloudRunJobInvoker(
                os.getenv("FACE_PROJECT_ID", "teak-banner-dome"),
                os.getenv("FACE_REGION", "us-central1"),
                os.getenv("FACE_INGEST_JOB", "face-ingest-drain"),
            )
        else:
            invoke_ingest = lambda _run_id: None
    if invoke_gpu_detect is None:
        invoke_gpu_detect = (
            CloudRunJobInvoker(
                os.getenv("FACE_PROJECT_ID", "teak-banner-dome"),
                os.getenv("FACE_REGION", "us-central1"),
                os.getenv("FACE_INTERACTIVE_JOB", "face-interactive-gpu"),
                env={"FACE_INTERACTIVE_MODE": "detect"},
            )
            if configured_repository
            else lambda _run_id: None
        )
    if invoke_gpu_match is None:
        invoke_gpu_match = (
            CloudRunJobInvoker(
                os.getenv("FACE_PROJECT_ID", "teak-banner-dome"),
                os.getenv("FACE_REGION", "us-central1"),
                os.getenv("FACE_INTERACTIVE_JOB", "face-interactive-gpu"),
                env={"FACE_INTERACTIVE_MODE": "match"},
            )
            if configured_repository
            else lambda _run_id: None
        )
    if invoke_maintenance is None:
        invoke_maintenance = (
            CloudRunJobInvoker(
                os.getenv("FACE_PROJECT_ID", "teak-banner-dome"),
                os.getenv("FACE_REGION", "us-central1"),
                os.getenv("FACE_INGEST_JOB", "face-ingest-drain"),
                env={"FACE_INGEST_MODE": "maintenance"},
            )
            if configured_repository
            else lambda _run_id: None
        )

    @app.before_request
    def authenticate_and_validate_origin():
        g.principal = _principal()
        operation = "create" if request.path == "/api/runs" else "read"
        limit = 10 if operation == "create" else 120
        if not rate_limiter.allow((g.principal, operation), limit):
            raise RequestError(429, "rate_limited", "Request rate limit exceeded.")
        if request.method not in {"GET", "HEAD", "OPTIONS"} and (
            not origin
            or not hmac.compare_digest(request.headers.get("Origin", ""), origin)
        ):
            raise RequestError(403, "origin_rejected", "Request Origin is not allowed.")

    @app.errorhandler(RequestError)
    def request_error(error):
        return _error(error.status, error.code, error.message)

    @app.errorhandler(RunNotFoundError)
    def not_found(_error_value):
        return _error(404, "run_not_found", "Run not found.")

    @app.errorhandler(RunConflictError)
    def conflict(error):
        return _error(409, "run_conflict", str(error))

    @app.after_request
    def secure_response(response: Response):
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.post("/api/runs")
    def create_run():
        key = request.headers.get("Idempotency-Key", "")
        if not IDEMPOTENCY_RE.fullmatch(key):
            raise RequestError(
                400,
                "invalid_idempotency_key",
                "Idempotency-Key must be 16–128 visible ASCII characters.",
            )
        payload = _create_payload(
            _json_object(), allow_arbitrary_hosts, enrollment_enabled
        )
        created = repository.create(g.principal, key, payload)
        if created.created and payload["source"]["kind"] == "url":
            invoke_ingest(created.record["run_id"])
        response = jsonify(created.record)
        response.status_code = 202
        response.headers["Location"] = created.record["status_url"]
        return response

    @app.get("/api/runs/<uuid:run_id>")
    def get_run(run_id: uuid.UUID):
        return jsonify(repository.get(str(run_id)))

    @app.post("/api/runs/<uuid:run_id>/upload-session")
    def upload_session(run_id: uuid.UUID):
        if upload_service is None:
            raise RequestError(
                503, "upload_unavailable", "Upload service is unavailable."
            )
        session_uri = upload_service.create_session(str(run_id))
        response = jsonify({"session_uri": session_uri})
        response.status_code = 201
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.post("/api/runs/<uuid:run_id>/upload-complete")
    def upload_complete(run_id: uuid.UUID):
        if upload_service is None:
            raise RequestError(
                503, "upload_unavailable", "Upload service is unavailable."
            )
        payload = _json_object()
        if (
            set(payload) != {"bytes", "sha256"}
            or type(payload["bytes"]) is not int
            or payload["bytes"] <= 0
        ):
            raise RequestError(
                422, "invalid_upload_completion", "bytes and SHA-256 are required."
            )
        try:
            digest = validate_sha256(payload["sha256"])
        except (TypeError, ValueError) as error:
            raise RequestError(
                422,
                "invalid_upload_completion",
                "SHA-256 must be 64 hexadecimal characters.",
            ) from error
        uploaded = upload_service.verify_completed(str(run_id))
        record = repository.finalize_upload(
            str(run_id),
            expected_bytes=payload["bytes"],
            expected_sha256=digest,
            object_generation=uploaded.generation,
            object_bytes=uploaded.size,
        )
        invoke_gpu_detect(record["run_id"])
        return jsonify(record), 202

    @app.get("/api/runs/<uuid:run_id>/face-groups")
    def face_groups(run_id: uuid.UUID):
        groups, version = repository.groups(str(run_id))
        response = jsonify({"groups": groups})
        response.set_etag(str(version))
        return response

    @app.get("/api/runs/<uuid:run_id>/face-groups/<uuid:group_id>/preview")
    def face_group_preview(run_id: uuid.UUID, group_id: uuid.UUID):
        if gallery_service is None:
            raise RequestError(
                503, "gallery_unavailable", "Gallery service is unavailable."
            )
        return Response(
            gallery_service.preview(str(run_id), str(group_id)),
            content_type="image/jpeg",
        )

    @app.get("/api/gallery/faces/<uuid:representative_id>")
    def representative_face(representative_id: uuid.UUID):
        if gallery_service is None:
            raise RequestError(
                503, "gallery_unavailable", "Gallery service is unavailable."
            )
        return Response(
            gallery_service.representative(str(representative_id)),
            content_type="image/jpeg",
        )

    @app.get("/api/subjects/<uuid:subject_id>")
    def subject(subject_id: uuid.UUID):
        if gallery_service is None:
            raise RequestError(
                503, "gallery_unavailable", "Gallery service is unavailable."
            )
        return jsonify(gallery_service.subject(str(subject_id)))

    @app.put("/api/runs/<uuid:run_id>/face-selection")
    def select_groups(run_id: uuid.UUID):
        payload = _json_object()
        if set(payload) != {"group_ids"} or not isinstance(payload["group_ids"], list):
            raise RequestError(422, "invalid_selection", "group_ids must be an array.")
        if not payload["group_ids"] or len(set(payload["group_ids"])) != len(
            payload["group_ids"]
        ):
            raise RequestError(
                422, "invalid_selection", "Select one or more unique face groups."
            )
        try:
            group_ids = [str(uuid.UUID(value)) for value in payload["group_ids"]]
        except (TypeError, ValueError, AttributeError) as error:
            raise RequestError(
                422, "invalid_selection", "Every group ID must be a UUID."
            ) from error
        record = repository.select(
            str(run_id), group_ids, _etag_version(request.headers.get("If-Match"))
        )
        invoke_gpu_match(record["run_id"])
        return jsonify(record), 202

    @app.get("/api/runs/<uuid:run_id>/results")
    def results(run_id: uuid.UUID):
        return jsonify(repository.results(str(run_id)))

    @app.post("/api/runs/<uuid:run_id>/retry")
    def retry(run_id: uuid.UUID):
        record = repository.retry(str(run_id))
        if record["state"] == "fetching":
            invoke_ingest(record["run_id"])
        elif record["state"] in {"queued", "detecting"}:
            invoke_gpu_detect(record["run_id"])
        elif record["state"] == "matching":
            invoke_gpu_match(record["run_id"])
        return jsonify(record), 202

    @app.post("/api/runs/<uuid:run_id>/cancel")
    def cancel(run_id: uuid.UUID):
        record = repository.cancel(str(run_id))
        if record["state"] == "cancelled":
            invoke_maintenance(record["run_id"])
        return jsonify(record), 202

    @app.delete("/api/sources/<uuid:source_id>")
    def delete_source(source_id: uuid.UUID):
        record = repository.tombstone_source(str(source_id), g.principal)
        invoke_maintenance(record["source_id"])
        return jsonify(record), 202

    @app.get("/")
    @app.get("/runs/<uuid:_run_id>")
    def index(_run_id=None):
        return send_from_directory(
            os.path.join(os.path.dirname(__file__), "..", "ui"), "index.html"
        )

    @app.get("/styles.css")
    def styles():
        return send_from_directory(
            os.path.join(os.path.dirname(__file__), "..", "ui"), "styles.css"
        )

    @app.get("/app.js")
    def javascript():
        return send_from_directory(
            os.path.join(os.path.dirname(__file__), "..", "ui"), "app.js"
        )

    return app


def main() -> None:
    create_app().run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")))


if __name__ == "__main__":
    main()
