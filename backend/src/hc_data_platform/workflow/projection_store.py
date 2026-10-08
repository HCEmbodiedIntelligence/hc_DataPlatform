"""Workflow-owned artifact retention policy and legacy import compatibility."""

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from hc_data_platform.object_storage.artifact_adapters import (
    LocalProjectionArtifactStore as LocalProjectionArtifactStore,
)
from hc_data_platform.object_storage.artifact_adapters import (
    S3ProjectionArtifactStore as S3ProjectionArtifactStore,
)


class S3ProjectionStagingSweeper:
    """Delete expired projection/alignment Arrow keys by exact object key."""

    _PREFIXES = ("staging/projections/", "staging/alignment/", "staging/prepared-media/")

    def __init__(
        self,
        client: Any,
        bucket: str,
        *,
        ttl: timedelta,
        clock: Any = lambda: datetime.now(timezone.utc),
    ) -> None:
        if not bucket or ttl <= timedelta(0):
            raise ValueError("projection staging bucket and positive TTL are required")
        self._client = client
        self._bucket = bucket
        self._ttl = ttl
        self._clock = clock

    def run_once(self) -> int:
        cutoff = self._clock() - self._ttl
        deleted = 0
        for prefix in self._PREFIXES:
            deleted += self._sweep_prefix(prefix, cutoff)
        return deleted

    def _sweep_prefix(self, prefix: str, cutoff: datetime) -> int:
        deleted = 0
        continuation: str | None = None
        while True:
            parameters: dict[str, object] = {
                "Bucket": self._bucket,
                "Prefix": prefix,
                "MaxKeys": 1_000,
            }
            if continuation is not None:
                parameters["ContinuationToken"] = continuation
            response = self._client.list_objects_v2(**parameters)
            contents = response.get("Contents", ())
            if not isinstance(contents, (list, tuple)):
                raise TypeError("projection staging listing returned invalid Contents")
            for item in contents:
                if not isinstance(item, Mapping):
                    continue
                key = item.get("Key")
                modified = item.get("LastModified")
                if (
                    not isinstance(key, str)
                    or not key.startswith(prefix)
                    or not (
                        key.endswith(".arrow")
                        or (prefix == "staging/prepared-media/" and key.endswith(".mp4"))
                    )
                    or not isinstance(modified, datetime)
                ):
                    continue
                if modified.tzinfo is None:
                    modified = modified.replace(tzinfo=timezone.utc)
                if modified > cutoff:
                    continue
                self._client.delete_object(Bucket=self._bucket, Key=key)
                deleted += 1
            if not response.get("IsTruncated"):
                return deleted
            token = response.get("NextContinuationToken")
            if not isinstance(token, str) or not token:
                raise RuntimeError("projection staging listing omitted its continuation token")
            continuation = token
