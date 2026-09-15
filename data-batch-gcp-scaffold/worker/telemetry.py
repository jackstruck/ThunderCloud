from __future__ import annotations

import json
import logging


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {"severity": record.levelname, "event": record.getMessage()}
        for field in (
            "job_id",
            "rollout_id",
            "work_item_id",
            "attempt",
            "error_code",
            "error_type",
            "error_message",
            "processed_by_task",
            "failed_by_task",
            "status",
        ):
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        return json.dumps(payload, separators=(",", ":"))


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)
