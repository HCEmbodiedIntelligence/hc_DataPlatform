from types import SimpleNamespace

import pytest

from hc_data_platform.core.context import (
    RequestContext,
    bind_request_context,
    reset_request_context,
)
from hc_data_platform.workflow.ingest_guard import IngestDatasetBusy, PostgresIngestDispatchGuard


class Connection:
    def __init__(self, answers):
        self.answers = iter(answers)
        self.closed = False
        self.committed = False
        self.queries = []

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def execute(self, sql, args):
        self.queries.append((sql, args))

    def fetchone(self):
        return next(self.answers)

    def commit(self):
        self.committed = True

    def rollback(self):
        pass

    def close(self):
        self.closed = True


@pytest.mark.parametrize(
    ("answers", "busy", "reserve", "retry"),
    [
        ([(False,)], True, False, False),
        ([(True,), None, (1,)], True, False, False),
        ([(True,), ("SUCCEEDED",)], False, False, False),
        ([(True,), None, None], False, True, False),
        ([(True,), ("TECHNICAL_FAILED",), (1,)], True, False, True),
        ([(True,), ("TECHNICAL_FAILED",), None], False, True, True),
    ],
)
def test_dataset_reservation_survives_launch_retries_and_excludes_other_writers(
    answers, busy, reserve, retry
):
    control = Connection(answers)
    pending = Connection([])
    connections = iter([control, pending])
    guard = PostgresIngestDispatchGuard(lambda: next(connections))
    request = SimpleNamespace(
        organization_id="org",
        project_id="project",
        region_code="region",
        dataset_id="dataset",
        rollout_id="rollout",
    )
    token = bind_request_context(
        RequestContext(
            organization_id="org", project_id="project", region_code="region", service_identity=True
        )
    )
    try:
        if busy:
            with pytest.raises(IngestDatasetBusy):
                guard.reserve(request, "workflow", retry_terminal=retry)
        else:
            guard.reserve(request, "workflow", retry_terminal=retry)
    finally:
        reset_request_context(token)
    assert control.closed
    assert pending.committed is reserve
    if reserve:
        params = pending.queries[0][1]
        assert params[2] == "workflow"
        assert params[8:10] == ("PENDING", "dispatch_pending")
        assert pending.closed
