from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from hc_data_platform.collection_tasks.postgres import PostgresCollectionTaskRepository


@pytest.mark.parametrize(
    ("status", "stage", "qc", "expected"),
    [
        ("CANCELLED", "cancelled", None, "CANCELLED"),
        ("CANCELLED", "cancelled", "PASS", "CANCELLED"),
        ("PENDING", "retry_wait", "PASS", "RETRY_PENDING"),
        ("PENDING", "retry_wait", None, "RETRY_PENDING"),
        ("TECHNICAL_FAILED", "aligned_media", "PASS", "TECHNICAL_FAILED"),
        ("CANCELLED", "cancelled", "RISK", "QUALITY_RISK"),
    ],
)
def test_cancelled_and_retrying_packages_are_not_reported_as_technical_failures(
    status, stage, qc, expected
):
    now = datetime.now(timezone.utc)
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchall.return_value = [
        ("package", "rollout", "robot", qc, status, stage, None, None, None, None, None, now, now)
    ]
    result = PostgresCollectionTaskRepository(lambda: connection).packages(
        "organization", "project", "task", "region", "dataset"
    )
    assert result[0].state.value == expected
    assert not result[0].visualizable
