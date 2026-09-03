from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol, cast
from urllib.parse import quote, unquote, urlparse

from .models import (
    AlignedMediaGenerationRequestV1,
    AlignedMediaObjectV1,
    AlignedMediaScopeV1,
    EncodedAlignedMediaV1,
    PublishedAlignedMediaV1,
)

_INTENT_NAME = "publication-intent.json"


class S3AlignedMediaClient(Protocol):
    def put_object(self, **kwargs: object) -> Mapping[str, object]: ...
    def head_object(self, **kwargs: object) -> Mapping[str, object]: ...
    def get_object(self, **kwargs: object) -> Mapping[str, object]: ...
    def delete_object(self, **kwargs: object) -> Mapping[str, object]: ...
    def list_objects_v2(self, **kwargs: object) -> Mapping[str, object]: ...


class S3PresignClient(Protocol):
    def generate_presigned_url(
        self, client_method: str, Params: dict[str, str], ExpiresIn: int
    ) -> str: ...


class S3StreamingBody(Protocol):
    def read(self, size: int = -1) -> bytes: ...

    def close(self) -> None: ...


def _component(value: str) -> str:
    if not value:
        raise ValueError("aligned media key components must be non-empty")
    return quote(value, safe="-._~")


def _source_file(encoded: EncodedAlignedMediaV1) -> Path:
    parsed = urlparse(encoded.file_uri)
    if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
        raise ValueError("encoded aligned media must be a local file URI")
    path = Path(unquote(parsed.path)).resolve()
    if path.name != "media.mp4" or not path.is_file():
        raise ValueError("encoded aligned media file is missing")
    return path


def _prefix(
    root: str,
    scope: AlignedMediaScopeV1,
    request: AlignedMediaGenerationRequestV1,
    artifact_key: str,
) -> str:
    return "/".join(
        (
            root.strip("/"),
            "organizations",
            _component(scope.organization_id),
            "projects",
            _component(scope.project_id),
            "datasets",
            _component(request.dataset_id),
            "versions",
            str(request.expected_dataset_version),
            "rollouts",
            _component(request.rollout_id),
            "cameras",
            _component(request.camera_id),
            "alignment",
            _component(request.alignment.alignment_version),
            "profiles",
            _component(request.profile_id),
            artifact_key,
        )
    )


class S3AlignedMediaArtifactStore:
    """Publish one immutable MP4 plus an exact, retry-stable intent receipt."""

    def __init__(
        self,
        client: S3AlignedMediaClient,
        bucket: str,
        *,
        presign_client: S3PresignClient | None = None,
        prefix: str = "aligned-media",
    ) -> None:
        self._client = client
        self._presign = presign_client or cast(S3PresignClient, client)
        self._bucket = bucket
        self._prefix = prefix.strip("/")

    def publish(
        self,
        *,
        scope: AlignedMediaScopeV1,
        request: AlignedMediaGenerationRequestV1,
        artifact_key: str,
        publication_token: str,
        encoded: EncodedAlignedMediaV1,
    ) -> PublishedAlignedMediaV1:
        source = _source_file(encoded)
        object_prefix = _prefix(self._prefix, scope, request, artifact_key)
        media_key = f"{object_prefix}/media.mp4"
        media_sha = _file_sha256(source)
        media_size = source.stat().st_size
        planned = AlignedMediaObjectV1(
            key=media_key,
            size=media_size,
            sha256=media_sha,
            media_type="video/mp4",
        )
        intent_key = f"{object_prefix}/{_INTENT_NAME}"
        intent = _intent_body(
            scope=scope,
            request=request,
            artifact_key=artifact_key,
            publication_token=publication_token,
            object_prefix=object_prefix,
            media=planned,
        )
        intent_sha = hashlib.sha256(intent).hexdigest()
        created: list[str] = []
        try:
            # Publish the exact cleanup plan before the large object. A hard
            # process/Pod failure after the MP4 PUT can then always be recovered
            # by the orphan reconciler from this immutable intent.
            made_intent, intent_etag = self._put_bytes_immutable(
                intent_key,
                intent,
                sha256=intent_sha,
                media_type="application/json",
            )
            if made_intent:
                created.append(intent_key)
            made_media, media_etag = self._put_file_immutable(
                media_key,
                source,
                size=media_size,
                sha256=media_sha,
                media_type="video/mp4",
            )
            if made_media:
                created.append(media_key)
            self._verify(media_key, size=media_size, sha256=media_sha)
            self._verify(intent_key, size=len(intent), sha256=intent_sha)
        except BaseException:
            for key in reversed(created):
                with suppress(Exception):
                    self._client.delete_object(Bucket=self._bucket, Key=key)
            raise
        media = planned.model_copy(update={"etag": media_etag})
        intent_object = AlignedMediaObjectV1(
            key=intent_key,
            size=len(intent),
            sha256=intent_sha,
            etag=intent_etag,
            media_type="application/json",
        )
        return PublishedAlignedMediaV1(
            object_prefix=object_prefix,
            media_object_key=media_key,
            objects=(media, intent_object),
            total_bytes=media_size + len(intent),
            content_sha256=media_sha,
            publication_token=publication_token,
            intent_key=intent_key,
            etag=media_etag,
        )

    def _put_file_immutable(
        self,
        key: str,
        path: Path,
        *,
        size: int,
        sha256: str,
        media_type: str,
    ) -> tuple[bool, str | None]:
        try:
            with path.open("rb") as body:
                response = self._client.put_object(
                    Bucket=self._bucket,
                    Key=key,
                    Body=body,
                    ContentLength=size,
                    ContentType=media_type,
                    Metadata={"sha256": sha256},
                    IfNoneMatch="*",
                )
        except Exception as exc:
            if not _is_precondition_failure(exc):
                raise
            self._verify(key, size=size, sha256=sha256)
            return False, _etag(self._client.head_object(Bucket=self._bucket, Key=key))
        return True, _etag(response)

    def _put_bytes_immutable(
        self, key: str, body: bytes, *, sha256: str, media_type: str
    ) -> tuple[bool, str | None]:
        try:
            response = self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=body,
                ContentType=media_type,
                Metadata={"sha256": sha256},
                IfNoneMatch="*",
            )
        except Exception as exc:
            if not _is_precondition_failure(exc):
                raise
            self._verify(key, size=len(body), sha256=sha256)
            return False, _etag(self._client.head_object(Bucket=self._bucket, Key=key))
        return True, _etag(response)

    def _verify(self, key: str, *, size: int, sha256: str) -> None:
        head = self._client.head_object(Bucket=self._bucket, Key=key)
        content_length = head.get("ContentLength")
        if (
            not isinstance(content_length, int)
            or content_length != size
            or not _metadata_matches(head.get("Metadata"), "sha256", sha256)
        ):
            raise RuntimeError("aligned media object verification failed")

    def authorize_object(self, object_key: str, *, expires_in: timedelta) -> str:
        ttl_seconds = max(1, int(expires_in.total_seconds()))
        return self._presign.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self._bucket,
                "Key": object_key,
                # The object key is immutable and content-addressed. Keep the
                # response in this user's browser cache for the grant lifetime
                # so seek/re-render does not repeatedly consume OSS NetworkOut.
                "ResponseCacheControl": f"private,max-age={ttl_seconds},immutable",
                "ResponseContentType": "video/mp4",
            },
            ExpiresIn=ttl_seconds,
        )

    def rollback_publication(self, publication: PublishedAlignedMediaV1) -> int:
        return self.delete_exact(publication.objects)

    def verify_publication(self, publication: PublishedAlignedMediaV1) -> bool:
        try:
            for item in publication.objects:
                self._verify(item.key, size=item.size, sha256=item.sha256)
        except Exception:
            return False
        return True

    def delete_exact(self, objects: Sequence[AlignedMediaObjectV1]) -> int:
        deleted = 0
        for item in objects:
            self._client.delete_object(Bucket=self._bucket, Key=item.key)
            deleted += item.size
        return deleted

    def resolve_local_object(self, object_key: str) -> Path | None:
        del object_key
        return None


class LocalAlignedMediaArtifactStore:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def publish(
        self,
        *,
        scope: AlignedMediaScopeV1,
        request: AlignedMediaGenerationRequestV1,
        artifact_key: str,
        publication_token: str,
        encoded: EncodedAlignedMediaV1,
    ) -> PublishedAlignedMediaV1:
        source = _source_file(encoded)
        object_prefix = _prefix("aligned-media", scope, request, artifact_key)
        target_dir = self._path(object_prefix)
        target_dir.parent.mkdir(parents=True, exist_ok=True)
        media_key = f"{object_prefix}/media.mp4"
        media_sha = _file_sha256(source)
        media_size = source.stat().st_size
        planned = AlignedMediaObjectV1(
            key=media_key,
            size=media_size,
            sha256=media_sha,
            media_type="video/mp4",
        )
        intent = _intent_body(
            scope=scope,
            request=request,
            artifact_key=artifact_key,
            publication_token=publication_token,
            object_prefix=object_prefix,
            media=planned,
        )
        intent_key = f"{object_prefix}/{_INTENT_NAME}"
        if not target_dir.exists():
            temporary = Path(tempfile.mkdtemp(prefix=f".{artifact_key}.", dir=target_dir.parent))
            try:
                shutil.copyfile(source, temporary / "media.mp4")
                (temporary / _INTENT_NAME).write_bytes(intent)
                os.replace(temporary, target_dir)
            finally:
                if temporary.exists():
                    shutil.rmtree(temporary)
        target_media = self._path(media_key)
        if target_media.stat().st_size != media_size or _file_sha256(target_media) != media_sha:
            raise RuntimeError("immutable aligned media collision")
        intent_sha = hashlib.sha256(intent).hexdigest()
        target_intent = self._path(intent_key)
        if target_intent.read_bytes() != intent:
            raise RuntimeError("immutable aligned media intent collision")
        return PublishedAlignedMediaV1(
            object_prefix=object_prefix,
            media_object_key=media_key,
            objects=(
                planned.model_copy(update={"etag": media_sha}),
                AlignedMediaObjectV1(
                    key=intent_key,
                    size=len(intent),
                    sha256=intent_sha,
                    etag=intent_sha,
                    media_type="application/json",
                ),
            ),
            total_bytes=media_size + len(intent),
            content_sha256=media_sha,
            publication_token=publication_token,
            intent_key=intent_key,
            etag=media_sha,
        )

    def authorize_object(self, object_key: str, *, expires_in: timedelta) -> str:
        del expires_in
        token = base64.urlsafe_b64encode(object_key.encode()).rstrip(b"=").decode()
        return f"/api/v1/aligned-media/local/{token}"

    def rollback_publication(self, publication: PublishedAlignedMediaV1) -> int:
        return self.delete_exact(publication.objects)

    def verify_publication(self, publication: PublishedAlignedMediaV1) -> bool:
        for item in publication.objects:
            path = self._path(item.key)
            if (
                not path.is_file()
                or path.stat().st_size != item.size
                or _file_sha256(path) != item.sha256
            ):
                return False
        return True

    def delete_exact(self, objects: Sequence[AlignedMediaObjectV1]) -> int:
        deleted = 0
        parents: set[Path] = set()
        for item in objects:
            path = self._path(item.key)
            parents.add(path.parent)
            if path.exists():
                path.unlink()
                deleted += item.size
        for parent in sorted(parents, key=lambda value: len(value.parts), reverse=True):
            with suppress(OSError):
                parent.rmdir()
        return deleted

    def resolve_local_object(self, object_key: str) -> Path | None:
        path = self._path(object_key)
        return path if path.is_file() else None

    def _path(self, key: str) -> Path:
        candidate = (self._root / key).resolve()
        if self._root not in candidate.parents and candidate != self._root:
            raise FileNotFoundError
        return candidate


def _intent_body(
    *,
    scope: AlignedMediaScopeV1,
    request: AlignedMediaGenerationRequestV1,
    artifact_key: str,
    publication_token: str,
    object_prefix: str,
    media: AlignedMediaObjectV1,
) -> bytes:
    value = {
        "schema_version": "aligned-media-publication-intent/v1",
        "scope": scope.model_dump(mode="json"),
        "request": request.model_dump(mode="json"),
        "artifact_key": artifact_key,
        "publication_token": publication_token,
        "object_prefix": object_prefix,
        "created_at": request.alignment.created_at.isoformat(),
        "objects": [media.model_dump(mode="json")],
    }
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _metadata_matches(metadata: object, name: str, expected: str) -> bool:
    if not isinstance(metadata, Mapping):
        return False
    normalized = name.casefold()
    return any(
        isinstance(key, str) and key.casefold() == normalized and value == expected
        for key, value in metadata.items()
    )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _etag(response: Mapping[str, object]) -> str | None:
    value = response.get("ETag")
    return value.strip('"') if isinstance(value, str) else None


def _is_precondition_failure(exc: Exception) -> bool:
    response = getattr(exc, "response", None)
    if not isinstance(response, Mapping):
        return False
    error = response.get("Error")
    return isinstance(error, Mapping) and str(error.get("Code", "")) in {
        "PreconditionFailed",
        "412",
    }


class S3AlignedMediaOrphanReconciler:
    """Delete expired unreferenced intent members by exact key, never by prefix."""

    def __init__(
        self,
        client: S3AlignedMediaClient,
        bucket: str,
        is_referenced: Callable[[AlignedMediaScopeV1, str, str, datetime], bool],
        *,
        prefix: str = "aligned-media",
        orphan_ttl: timedelta = timedelta(hours=2),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._client = client
        self._bucket = bucket
        self._is_referenced = is_referenced
        self._prefix = f"{prefix.strip('/')}/organizations/"
        self._ttl = orphan_ttl
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def run_once(self) -> int:
        now = self._clock()
        cutoff = now - self._ttl
        deleted = 0
        continuation: str | None = None
        while True:
            arguments: dict[str, object] = {
                "Bucket": self._bucket,
                "Prefix": self._prefix,
                "MaxKeys": 1_000,
            }
            if continuation is not None:
                arguments["ContinuationToken"] = continuation
            response = self._client.list_objects_v2(**arguments)
            contents = response.get("Contents", ())
            if not isinstance(contents, (list, tuple)):
                raise TypeError("aligned media orphan listing is invalid")
            for item in contents:
                if not isinstance(item, Mapping):
                    continue
                key = item.get("Key")
                modified = item.get("LastModified")
                if (
                    not isinstance(key, str)
                    or not key.endswith(f"/{_INTENT_NAME}")
                    or not isinstance(modified, datetime)
                    or modified > cutoff
                ):
                    continue
                deleted += self._reconcile(key, now)
            if not response.get("IsTruncated"):
                return deleted
            token = response.get("NextContinuationToken")
            if not isinstance(token, str) or not token:
                raise RuntimeError("aligned media orphan listing omitted continuation token")
            continuation = token

    def _reconcile(self, intent_key: str, now: datetime) -> int:
        response = self._client.get_object(Bucket=self._bucket, Key=intent_key)
        body = cast(S3StreamingBody, response["Body"])
        try:
            raw = body.read(2 * 1024 * 1024 + 1)
        finally:
            body.close()
        value = json.loads(raw)
        if value.get("schema_version") != "aligned-media-publication-intent/v1":
            raise ValueError("aligned media publication intent schema is invalid")
        scope = AlignedMediaScopeV1.model_validate(value.get("scope"))
        artifact_key = str(value.get("artifact_key", ""))
        publication_token = str(value.get("publication_token", ""))
        if self._is_referenced(scope, artifact_key, publication_token, now):
            return 0
        raw_objects = value.get("objects")
        if not isinstance(raw_objects, list):
            raise ValueError("aligned media publication intent members are invalid")
        objects = tuple(AlignedMediaObjectV1.model_validate(item) for item in raw_objects)
        object_prefix = str(value.get("object_prefix", ""))
        if intent_key != f"{object_prefix}/{_INTENT_NAME}" or any(
            not item.key.startswith(f"{object_prefix}/") for item in objects
        ):
            raise ValueError("aligned media publication intent identity is invalid")
        for item in objects:
            self._client.delete_object(Bucket=self._bucket, Key=item.key)
        self._client.delete_object(Bucket=self._bucket, Key=intent_key)
        return len(objects) + 1
