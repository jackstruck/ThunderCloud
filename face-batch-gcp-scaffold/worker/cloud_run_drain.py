from __future__ import annotations

import argparse
import logging
import os
import signal
import threading
import time
import uuid

from .config import Settings
from .db import Database, Versions
from .process import VideoProcessor
from .queue import QueueDatabase, WorkItem
from .telemetry import configure_logging


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Drain a Cloud SQL rollout queue")
    result.add_argument("--rollout-id", default=os.getenv("FACE_ROLLOUT_ID"))
    result.add_argument(
        "--soft-deadline-seconds",
        type=int,
        default=int(os.getenv("FACE_SOFT_DEADLINE_SECONDS", "3120")),
    )
    result.add_argument(
        "--lease-minutes", type=int, default=int(os.getenv("FACE_LEASE_MINUTES", "20"))
    )
    return result


def classify_failure(error: Exception) -> tuple[str, bool]:
    name = type(error).__name__.upper()
    message = str(error).lower()
    if isinstance(error, ValueError):
        return "INVALID_INPUT", False
    if "checksum" in message or "sha-256" in message:
        return "CHECKSUM_MISMATCH", False
    if "customer-supplied encryption" in message:
        return "CSEK_POLICY_FAILURE", False
    if name in {"TOOMANYREQUESTS", "SERVICEUNAVAILABLE", "INTERNALSERVERERROR"}:
        return "TRANSIENT_CLOUD_API", True
    if any(token in message for token in ("429", "temporar", "timeout", "connection")):
        return "TRANSIENT_RUNTIME", True
    return "UNKNOWN_FAILURE", True


class LeaseHeartbeat:
    def __init__(self, queue: QueueDatabase, item: WorkItem, lease_minutes: int):
        self.queue = queue
        self.item = item
        self.lease_minutes = lease_minutes
        self.stop_event = threading.Event()
        self.lost = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        interval = max(30, min(60, self.lease_minutes * 30))
        while not self.stop_event.wait(interval):
            try:
                if not self.queue.heartbeat(self.item, self.lease_minutes):
                    self.lost.set()
                    return
            except Exception:
                logging.getLogger(__name__).exception(
                    "lease_heartbeat_failed",
                    extra={"work_item_id": self.item.work_item_id},
                )

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop_event.set()
        self.thread.join(timeout=5)


def _metadata(item: WorkItem) -> dict[str, str]:
    result = {
        "external-source-ref": item.source_uri,
        "request-id": item.work_item_id,
    }
    if item.manifest_uid:
        result["source-uid"] = item.manifest_uid
    if item.source_sha256:
        result["sha256"] = item.source_sha256
    return result


def drain(
    rollout_id: str,
    settings: Settings,
    *,
    soft_deadline_seconds: int = 3120,
    lease_minutes: int = 20,
    monotonic=time.monotonic,
) -> dict:
    uuid.UUID(rollout_id)
    if not settings.matching_enabled:
        raise RuntimeError("Cloud Run rollout refuses FACE_MATCHING_ENABLED=false")
    if settings.cloud_sql_ip_type != "PRIVATE":
        raise RuntimeError("Cloud Run rollout requires private Cloud SQL routing")
    if not settings.csek_secret or settings.csek_file is not None:
        raise RuntimeError("Cloud Run rollout requires in-memory Secret Manager CSEK")
    if not settings.require_cuda:
        raise RuntimeError("Cloud Run rollout requires FACE_REQUIRE_CUDA=true")
    if soft_deadline_seconds < 300:
        raise ValueError("soft deadline must leave meaningful processing time")

    queue_db = Database(
        settings.cloud_sql_instance,
        settings.db_user,
        settings.db_name,
        settings.cloud_sql_ip_type,
    )
    queue = QueueDatabase(queue_db)
    processor = VideoProcessor(settings)
    storage = processor.storage
    started = monotonic()
    stopping = threading.Event()
    previous_handlers = {}

    def request_stop(_signum, _frame):
        stopping.set()

    for signum in (signal.SIGTERM, signal.SIGINT):
        previous_handlers[signum] = signal.signal(signum, request_stop)

    processed = failed = 0
    try:
        queue.assert_worker_contract(
            rollout_id,
            Versions(
                settings.worker_version,
                settings.detector_version,
                settings.embedding_model_version,
                settings.threshold_version,
            ),
            settings.matching_enabled,
        )
        while not stopping.is_set() and monotonic() - started < soft_deadline_seconds:
            remaining = soft_deadline_seconds - (monotonic() - started)
            if remaining <= 180:
                break
            expired = queue.expire_exhausted_leases(rollout_id)
            if expired:
                logging.getLogger(__name__).error(
                    "work_item_dead_letter",
                    extra={
                        "rollout_id": rollout_id,
                        "error_code": "TASK_LEASE_EXPIRED",
                    },
                )
            # Current measurements are around 1.3 seconds/MiB. Three seconds/MiB plus
            # two minutes fixed overhead is deliberately conservative.
            max_source_bytes = max(0, int((remaining - 120) / 3 * 1024 * 1024))
            item = queue.claim(
                rollout_id,
                lease_minutes,
                max_source_bytes=max_source_bytes,
            )
            if item is None:
                break
            try:
                staging_uri = item.staged_uri
                generation = item.staged_generation
                if staging_uri:
                    try:
                        generation, _ = storage.verify_staging(staging_uri, generation)
                    except Exception as error:
                        if type(error).__name__ != "NotFound":
                            raise
                        staging_uri = None
                        generation = None
                if staging_uri is None:
                    staging_uri, generation, _ = storage.stage(
                        item.source_uri, _metadata(item)
                    )
                    queue.record_staging(item, staging_uri, generation)
                with LeaseHeartbeat(queue, item, lease_minutes) as heartbeat:
                    processor.process(
                        gcs_uri=staging_uri,
                        external_source_ref=item.source_uri,
                        expected_sha256=item.source_sha256,
                        job_id=item.application_job_id,
                        staging_generation=generation,
                        source_metadata=item.source_metadata,
                    )
                    if heartbeat.lost.is_set():
                        raise RuntimeError("work-item lease ownership was lost")
                queue.succeed(item)
                logging.getLogger(__name__).info(
                    "work_item_succeeded",
                    extra={
                        "rollout_id": rollout_id,
                        "work_item_id": item.work_item_id,
                        "attempt": item.attempt_count,
                    },
                )
                processed += 1
            except Exception as error:
                code, retryable = classify_failure(error)
                logging.getLogger(__name__).exception(
                    "work_item_failed",
                    extra={
                        "rollout_id": rollout_id,
                        "work_item_id": item.work_item_id,
                        "attempt": item.attempt_count,
                        "error_code": code,
                        "error_type": type(error).__name__,
                        "error_message": str(error),
                    },
                )
                final_state = queue.fail(item, code, retryable)
                if final_state == "dead_letter":
                    logging.getLogger(__name__).error(
                        "work_item_dead_letter",
                        extra={
                            "rollout_id": rollout_id,
                            "work_item_id": item.work_item_id,
                            "error_code": code,
                        },
                    )
                failed += 1
        result = queue.refresh_rollout(rollout_id)
        return {**result, "processed_by_task": processed, "failed_by_task": failed}
    finally:
        processor.close()
        queue_db.close()
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)


def main(argv=None) -> None:
    args = parser().parse_args(argv)
    if not args.rollout_id:
        raise ValueError("--rollout-id or FACE_ROLLOUT_ID is required")
    configure_logging()
    result = drain(
        args.rollout_id,
        Settings.from_env(),
        soft_deadline_seconds=args.soft_deadline_seconds,
        lease_minutes=args.lease_minutes,
    )
    logging.getLogger(__name__).info("drain_finished", extra=result)


if __name__ == "__main__":
    main()
