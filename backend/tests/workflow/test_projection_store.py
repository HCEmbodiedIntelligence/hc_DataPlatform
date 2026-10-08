from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from hc_data_platform.workflow.projection_store import S3ProjectionStagingSweeper

NOW = datetime(2026, 8, 28, tzinfo=timezone.utc)


class _ListingClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.deleted: list[tuple[str, str]] = []

    def list_objects_v2(self, **kwargs: object) -> dict[str, Any]:
        self.calls.append(dict(kwargs))
        if kwargs["Prefix"] == "staging/prepared-media/":
            return {
                "IsTruncated": False,
                "Contents": [
                    {
                        "Key": "staging/prepared-media/old/media.mp4",
                        "LastModified": NOW - timedelta(hours=25),
                    },
                    {
                        "Key": "staging/prepared-media/recent/media.mp4",
                        "LastModified": NOW - timedelta(hours=1),
                    },
                    {"Key": "raw/original.mp4", "LastModified": NOW - timedelta(days=30)},
                    {
                        "Key": "aligned-media/published.mp4",
                        "LastModified": NOW - timedelta(days=30),
                    },
                ],
            }
        if "ContinuationToken" not in kwargs:
            return {
                "IsTruncated": True,
                "NextContinuationToken": "page-2",
                "Contents": [
                    {
                        "Key": "staging/projections/project/source/arrow-projection-v1.arrow",
                        "LastModified": NOW - timedelta(hours=25),
                    },
                    {
                        "Key": "staging/projections/project/recent.arrow",
                        "LastModified": NOW - timedelta(hours=1),
                    },
                ],
            }
        return {
            "IsTruncated": False,
            "Contents": [
                {
                    "Key": "derived/frame-selections/project/source/adaptive-2fps-v1.json",
                    "LastModified": NOW - timedelta(days=30),
                },
                {
                    "Key": "staging/projections/project/source/other.txt",
                    "LastModified": NOW - timedelta(days=30),
                },
            ],
        }

    def delete_object(self, *, Bucket: str, Key: str) -> None:
        self.deleted.append((Bucket, Key))


def test_sweeper_deletes_only_expired_staging_and_preserves_raw_and_published_media() -> None:
    client = _ListingClient()
    sweeper = S3ProjectionStagingSweeper(
        client,
        "data-bucket",
        ttl=timedelta(hours=24),
        clock=lambda: NOW,
    )

    assert sweeper.run_once() == 2
    assert client.deleted == [
        (
            "data-bucket",
            "staging/projections/project/source/arrow-projection-v1.arrow",
        ),
        ("data-bucket", "staging/prepared-media/old/media.mp4"),
    ]
    assert [call["Prefix"] for call in client.calls] == [
        "staging/projections/",
        "staging/projections/",
        "staging/alignment/",
        "staging/alignment/",
        "staging/prepared-media/",
    ]
    assert client.calls[1]["ContinuationToken"] == "page-2"
