from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from .models import (
    EncodedPreviewArtifactV1,
    PreviewCacheRecordV1,
    PreviewFrameV1,
    PreviewRequestV1,
    RenderFrameV1,
    StepRangeV1,
)


class InMemoryStepReader:
    def __init__(self, frames: Sequence[PreviewFrameV1] = ()) -> None:
        self.frames = list(frames)
        self.calls = 0

    def read_steps(
        self,
        *,
        project_id: str,
        dataset_id: str,
        rollout_id: str,
        lance_version: str,
        camera_id: str,
        start_step: int | None,
        end_step: int | None,
    ) -> Sequence[PreviewFrameV1]:
        del project_id, dataset_id, lance_version, camera_id
        self.calls += 1
        return tuple(
            frame
            for frame in self.frames
            if frame.rollout_id == rollout_id
            and (start_step is None or frame.step_index >= start_step)
            and (end_step is None or frame.step_index < end_step)
        )


class InMemoryExclusionReader:
    def __init__(self, ranges: Sequence[StepRangeV1] = ()) -> None:
        self.ranges = tuple(ranges)
        self.calls = 0

    def effective_ranges(
        self, *, project_id: str, rollout_id: str, annotation_revision: int
    ) -> Sequence[StepRangeV1]:
        del project_id, rollout_id, annotation_revision
        self.calls += 1
        return self.ranges


class InMemoryMediaEncoder:
    """Captures encoder inputs and returns a deterministic fake HLS artifact."""

    def __init__(self) -> None:
        self.calls: list[tuple[RenderFrameV1, ...]] = []

    def encode(
        self, *, cache_key: str, request: PreviewRequestV1, frames: Sequence[RenderFrameV1]
    ) -> EncodedPreviewArtifactV1:
        captured = tuple(frames)
        self.calls.append(captured)
        return EncodedPreviewArtifactV1(
            artifact_uri=f"memory://previews/{cache_key}/index.m3u8",
            duration_seconds=len(captured) / request.frequency_hz if captured else 0,
            frame_count=len(captured),
        )


class InMemoryPreviewCache:
    def __init__(self) -> None:
        self._by_key: dict[str, PreviewCacheRecordV1] = {}
        self._by_session: dict[str, PreviewCacheRecordV1] = {}

    def get(self, cache_key: str, *, now: datetime) -> PreviewCacheRecordV1 | None:
        record = self._by_key.get(cache_key)
        if record is None or record.cache_expires_at <= now:
            return None
        return record

    def get_by_session(self, session_id: str, *, now: datetime) -> PreviewCacheRecordV1 | None:
        record = self._by_session.get(session_id)
        if record is None or record.cache_expires_at <= now:
            return None
        return record

    def put(self, record: PreviewCacheRecordV1) -> None:
        self._by_key[record.cache_key] = record
        self._by_session[record.session_id] = record


class HmacUrlSigner:
    """Signs relative API capability URLs, never object-store or file URLs."""

    def __init__(self, secret: bytes = b"preview-development-only") -> None:
        self._secret = secret

    def sign(
        self,
        *,
        session_id: str,
        artifact_uri: str,
        asset_name: str,
        expires_at: datetime,
    ) -> str:
        expires = int(expires_at.timestamp())
        signature = self._signature(
            session_id=session_id,
            artifact_uri=artifact_uri,
            asset_name=asset_name,
            expires=expires,
        )
        return (
            f"/api/v1/previews/sessions/{quote(session_id, safe='')}/media/"
            f"{quote(asset_name, safe='')}?expires={expires}&sig={signature}"
        )

    def verify(
        self,
        *,
        session_id: str,
        artifact_uri: str,
        asset_name: str,
        expires: int,
        signature: str,
        now: datetime,
    ) -> bool:
        if now.tzinfo is None:
            raise ValueError("preview signature clock must be timezone-aware")
        if expires <= int(now.astimezone(timezone.utc).timestamp()):
            return False
        expected = self._signature(
            session_id=session_id,
            artifact_uri=artifact_uri,
            asset_name=asset_name,
            expires=expires,
        )
        return hmac.compare_digest(expected, signature)

    def _signature(
        self,
        *,
        session_id: str,
        artifact_uri: str,
        asset_name: str,
        expires: int,
    ) -> str:
        payload = json.dumps(
            {
                "artifact_uri": artifact_uri,
                "asset_name": asset_name,
                "expires": expires,
                "session_id": session_id,
                "version": 1,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hmac.new(self._secret, payload, hashlib.sha256).hexdigest()


class InMemoryPreviewMediaReader:
    """The in-memory preview composition has no physical media to expose."""

    def resolve(self, record: PreviewCacheRecordV1, *, asset_name: str) -> Path:
        del record, asset_name
        raise FileNotFoundError
