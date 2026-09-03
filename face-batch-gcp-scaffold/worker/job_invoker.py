from __future__ import annotations

import logging


class CloudRunJobInvoker:
    def __init__(
        self,
        project: str,
        region: str,
        job: str,
        session=None,
        env: dict[str, str] | None = None,
    ):
        self.url = (
            "https://run.googleapis.com/v2/"
            f"projects/{project}/locations/{region}/jobs/{job}:run"
        )
        if session is None:
            import google.auth
            from google.auth.transport.requests import AuthorizedSession

            credentials, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            session = AuthorizedSession(credentials)
        self.session = session
        self.env = env or {}

    def __call__(self, run_id: str) -> None:
        try:
            response = self.session.post(
                self.url,
                json={
                    "overrides": {
                        "containerOverrides": [
                            {
                                "name": "worker",
                                "env": [
                                    {"name": "FACE_RUN_ID", "value": run_id},
                                    *(
                                        {"name": name, "value": value}
                                        for name, value in sorted(self.env.items())
                                    ),
                                ],
                            }
                        ],
                        "taskCount": 1,
                    }
                },
                timeout=10,
            )
            response.raise_for_status()
        except Exception:
            # The durable queue is authoritative. Scheduler reconciliation invokes the
            # same drain later, so an invocation-plane outage must not undo run creation.
            logging.getLogger(__name__).exception(
                "failed to invoke ingestion drain for run_id=%s", run_id
            )
