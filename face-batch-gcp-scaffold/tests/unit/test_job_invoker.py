from worker.job_invoker import CloudRunJobInvoker


class Response:
    def raise_for_status(self):
        pass


class Session:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error:
            raise self.error
        return Response()


def test_invokes_single_task_with_run_locator_only():
    session = Session()
    invoker = CloudRunJobInvoker("project", "region", "face-ingest-drain", session)
    invoker("run-id")
    assert session.calls[0][0].endswith(
        "/projects/project/locations/region/jobs/face-ingest-drain:run"
    )
    assert session.calls[0][1]["json"] == {
        "overrides": {
            "containerOverrides": [
                {
                    "name": "worker",
                    "env": [{"name": "FACE_RUN_ID", "value": "run-id"}],
                }
            ],
            "taskCount": 1,
        }
    }


def test_adds_sorted_fixed_mode_override():
    session = Session()
    invoker = CloudRunJobInvoker(
        "project", "region", "job", session, env={"FACE_INTERACTIVE_MODE": "match"}
    )
    invoker("run-id")
    assert session.calls[0][1]["json"]["overrides"]["containerOverrides"][0]["env"] == [
        {"name": "FACE_RUN_ID", "value": "run-id"},
        {"name": "FACE_INTERACTIVE_MODE", "value": "match"},
    ]


def test_invocation_failure_is_left_for_durable_reconciliation(caplog):
    invoker = CloudRunJobInvoker(
        "project", "region", "face-ingest-drain", Session(RuntimeError("outage"))
    )
    invoker("run-id")
    assert "failed to invoke ingestion drain" in caplog.text
