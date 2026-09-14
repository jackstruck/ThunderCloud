from __future__ import annotations

import os
from types import SimpleNamespace

from google.api_core.exceptions import GoogleAPIError

from .config import Settings
from .db import Database
from .hls_fetch import fetch_hotscope_video
from .ingest_repository import IngestRepository
from .job_invoker import CloudRunJobInvoker
from .maintenance import MaintenanceRepository, maintain
from .secure_fetch import fetch_media
from .source_adapters import resolve_source
from .storage import StorageRepository, load_configured_csek
from .telemetry import configure_logging


def drain(
    repository,
    storage,
    *,
    max_bytes: int,
    fetcher=fetch_media,
    resolver=resolve_source,
    hls_fetcher=fetch_hotscope_video,
    invoke_detect=lambda _run_id: None,
) -> int:
    completed = 0
    while work := repository.claim_fetch():
        try:
            if work.source_kind == "archive":
                object_name, generation, size = storage.archive_to_temporary(
                    work, max_bytes
                )
                source = SimpleNamespace(source_adapter="archive-object")
                fetched = SimpleNamespace(
                    final_url="",
                    content_type=work.content_type,
                    sha256=work.archive_sha256,
                )
            else:
                source = resolver(work.source_url)
                if source.source_adapter == "hotscope":
                    fetched = hls_fetcher(source.final_url, page_url=source.page_url, max_bytes=max_bytes)
                else:
                    fetched = fetcher(source.final_url, max_bytes=max_bytes)
                object_name, generation, size = storage.upload_temporary(
                    work.run_id,
                    fetched.data,
                    fetched.content_type,
                    fetched.sha256,
                )
            if repository.complete_fetch(
                work,
                final_url=fetched.final_url,
                source_adapter=source.source_adapter,
                content_type=fetched.content_type,
                sha256=fetched.sha256,
                object_name=object_name,
                generation=generation,
                size=size,
            ):
                completed += 1
                invoke_detect(work.run_id)
        except (ValueError, RuntimeError, GoogleAPIError) as error:
            rejected = isinstance(error, ValueError)
            repository.fail_fetch(
                work,
                "source_rejected" if rejected else "fetch_failed",
                retryable=not rejected,
            )
    return completed


def main() -> None:
    configure_logging()
    settings = Settings.from_env()
    settings.validate()
    database = Database(
        settings.cloud_sql_instance,
        settings.db_user,
        settings.db_name,
        settings.cloud_sql_ip_type,
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
    try:
        if os.getenv("FACE_INGEST_MODE", "drain") == "maintenance":
            maintain(MaintenanceRepository(database), storage)
        else:
            invoker = CloudRunJobInvoker(
                settings.project_id,
                os.getenv("FACE_REGION", "us-central1"),
                os.getenv("FACE_INTERACTIVE_JOB", "face-interactive-gpu"),
                env={"FACE_INTERACTIVE_MODE": "detect"},
            )
            drain(
                IngestRepository(database),
                storage,
                max_bytes=int(os.getenv("FACE_MAX_INGEST_BYTES", "262144000")),
                invoke_detect=invoker,
            )
    finally:
        database.close()


if __name__ == "__main__":
    main()
