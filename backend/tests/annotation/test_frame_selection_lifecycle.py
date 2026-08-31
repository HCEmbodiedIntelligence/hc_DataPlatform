from __future__ import annotations

from datetime import datetime, timezone

from hc_data_platform.annotation.frame_selection_lifecycle import (
    FrameSelectionDeletionReceipt,
    FrameSelectionLifecycleCollector,
)

NOW = datetime(2026, 8, 28, tzinfo=timezone.utc)


class _Repository:
    def __init__(self) -> None:
        self.receipt = FrameSelectionDeletionReceipt(
            task_id="task-1",
            object_key="derived/frame-selection/exact.json",
            size_bytes=123,
            deletion_token="25c08c94-8a76-4c7d-8396-1571e9b92483",
        )
        self.finished = False
        self.failures: list[str] = []

    def claim_expired(self, *, now: datetime) -> FrameSelectionDeletionReceipt | None:
        assert now == NOW
        return None if self.finished else self.receipt

    def finish_deletion(self, receipt: FrameSelectionDeletionReceipt) -> bool:
        assert receipt == self.receipt
        self.finished = True
        return True

    def record_failure(self, receipt: FrameSelectionDeletionReceipt, error_code: str) -> None:
        assert receipt == self.receipt
        self.failures.append(error_code)

    def used_bytes(self) -> int:
        return 0 if self.finished else self.receipt.size_bytes


class _ExactObjects:
    def __init__(self) -> None:
        self.keys: list[str] = []

    def delete(self, key: str) -> None:
        self.keys.append(key)


def test_collector_deletes_only_manifest_exact_key_then_releases_receipt() -> None:
    repository = _Repository()
    objects = _ExactObjects()
    collector = FrameSelectionLifecycleCollector(
        repository,
        objects,
        clock=lambda: NOW,
    )

    assert collector.run_once() == 1
    assert objects.keys == ["derived/frame-selection/exact.json"]
    assert repository.finished


def test_failed_exact_delete_keeps_fenced_receipt_for_safe_retry() -> None:
    repository = _Repository()

    class FailsOnce(_ExactObjects):
        def delete(self, key: str) -> None:
            super().delete(key)
            if len(self.keys) == 1:
                raise OSError("injected object-store outage")

    objects = FailsOnce()
    collector = FrameSelectionLifecycleCollector(
        repository,
        objects,
        clock=lambda: NOW,
        batch_size=1,
    )

    assert collector.run_once() == 0
    assert not repository.finished
    assert repository.failures == ["OSError"]
    assert collector.run_once() == 1
    assert objects.keys == [
        "derived/frame-selection/exact.json",
        "derived/frame-selection/exact.json",
    ]
