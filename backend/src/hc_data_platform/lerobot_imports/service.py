from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from typing import BinaryIO, cast

from hc_data_platform.core.context import select_request_scope
from hc_data_platform.core.errors import problem
from hc_data_platform.ingest.models import CompletedPart, PartAuthorization, utc_now
from hc_data_platform.ingest.ports import MultipartPart, ObjectStoragePort
from hc_data_platform.ingest.raw_sources import (
    CommittedRawSourceGraph,
    InMemoryRawSourceRepository,
    RawIngestJob,
    RawIngestJobType,
    RawSource,
    RawSourceEpisode,
    RawSourceFormat,
    RawSourceRepositoryPort,
)
from hc_data_platform.security.auth import AuthContext
from hc_data_platform.security.scope import ScopeGuard

from .models import (
    AuthorizeLeRobotPartsV1,
    CommitLeRobotImportV1,
    CompleteLeRobotAssetV1,
    CreateLeRobotImportV1,
    LeRobotAssetCompletedV1,
    LeRobotAssetUploadGrantV1,
    LeRobotImportAcceptedV1,
    LeRobotImportGrantV1,
    LeRobotPartGrantV1,
    lerobot_part_size,
)
from .orchestration import build_import_plan


class LeRobotWebUploadService:
    def __init__(
        self,
        storage: ObjectStoragePort,
        *,
        raw_sources: RawSourceRepositoryPort | None = None,
        authorization_ttl_seconds: int = 900,
    ) -> None:
        if not 1 <= authorization_ttl_seconds <= 3600:
            raise ValueError("authorization TTL must be between 1 and 3600 seconds")
        self._storage = storage
        self.raw_sources = raw_sources or InMemoryRawSourceRepository()
        self._authorization_ttl = authorization_ttl_seconds

    def begin(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
        manifest: CreateLeRobotImportV1,
    ) -> LeRobotImportGrantV1:
        self._authorize(
            auth,
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
        )
        manifest_document = _canonical_manifest(manifest)
        import_id = _stable_import_id(
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
            manifest=manifest,
        )
        root = self._root(organization_id, manifest.dataset_id, import_id)
        resumed = self._existing_grant(
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
            manifest=manifest,
            manifest_document=manifest_document,
            import_id=import_id,
        )
        if resumed is not None:
            return resumed
        started: list[tuple[str, str]] = []
        grants: list[LeRobotAssetUploadGrantV1] = []
        try:
            for source in manifest.files:
                key = f"{root}/source/{source.path}"
                upload_id = self._storage.create_multipart(key)
                started.append((key, upload_id))
                grants.append(
                    LeRobotAssetUploadGrantV1(
                        path=source.path,
                        multipart_upload_id=upload_id,
                        # Authorize just in time per file so unused URLs do not expire
                        # while an earlier large source object is still transferring.
                        parts=(),
                    )
                )
            self._storage.put_json(
                f"{root}/upload-session.json",
                {
                    "schema_version": "lerobot-web-upload-session/v1",
                    "organization_id": organization_id,
                    "project_id": project_id,
                    "region_code": region_code,
                    "upload_identity_version": "lerobot-web-upload-identity/v1",
                    "manifest": manifest_document,
                    "assets": [
                        {
                            "path": asset.path,
                            "multipart_upload_id": asset.multipart_upload_id,
                            "size": source.size,
                            "part_count": source.part_count,
                        }
                        for asset, source in zip(grants, manifest.files, strict=True)
                    ],
                },
                if_none_match=True,
            )
        except Exception:
            for key, upload_id in started:
                self._storage.abort_multipart(key, upload_id)
            resumed = self._existing_grant(
                organization_id=organization_id,
                project_id=project_id,
                region_code=region_code,
                manifest=manifest,
                manifest_document=manifest_document,
                import_id=import_id,
            )
            if resumed is not None:
                return resumed
            raise
        return LeRobotImportGrantV1(import_id=import_id, assets=tuple(grants))

    def _existing_grant(
        self,
        *,
        organization_id: str,
        project_id: str,
        region_code: str,
        manifest: CreateLeRobotImportV1,
        manifest_document: dict[str, object],
        import_id: str,
    ) -> LeRobotImportGrantV1 | None:
        session_key = (
            f"{self._root(organization_id, manifest.dataset_id, import_id)}/upload-session.json"
        )
        if self._storage.head(session_key) is None:
            return None
        session = self._require_session(
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
            dataset_id=manifest.dataset_id,
            import_id=import_id,
        )
        if session.get("manifest") != manifest_document:
            raise problem(
                status=409,
                code="LEROBOT_IMPORT_MANIFEST_CHANGED",
                title="LeRobot import manifest changed",
                detail=(
                    "This dataset already has an upload session with a different "
                    "collection binding or source directory manifest."
                ),
            )
        values = session.get("assets")
        if not isinstance(values, list):
            raise RuntimeError("stored LeRobot upload session assets are unreadable")
        grants: list[LeRobotAssetUploadGrantV1] = []
        for value in values:
            if not isinstance(value, dict):
                raise RuntimeError("stored LeRobot upload session asset is unreadable")
            path = value.get("path")
            multipart_upload_id = value.get("multipart_upload_id")
            size = value.get("size")
            if (
                not isinstance(path, str)
                or not isinstance(multipart_upload_id, str)
                or not isinstance(size, int)
            ):
                raise RuntimeError("stored LeRobot upload session asset is unreadable")
            metadata = self._storage.head(
                self._key(organization_id, manifest.dataset_id, import_id, path)
            )
            if metadata is not None and metadata.size != size:
                raise problem(
                    status=409,
                    code="LEROBOT_ASSET_SIZE_MISMATCH",
                    title="LeRobot asset size mismatch",
                    detail="The completed source object no longer matches its declaration.",
                )
            grants.append(
                LeRobotAssetUploadGrantV1(
                    path=path,
                    multipart_upload_id=multipart_upload_id,
                    completed=metadata is not None,
                )
            )
        return LeRobotImportGrantV1(import_id=import_id, assets=tuple(grants))

    def authorize_parts(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
        import_id: str,
        command: AuthorizeLeRobotPartsV1,
    ) -> LeRobotPartGrantV1:
        self._authorize(
            auth,
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
        )
        session = self._require_session(
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
            dataset_id=command.dataset_id,
            import_id=import_id,
        )
        asset = self._require_asset(
            session,
            path=command.path,
            multipart_upload_id=command.multipart_upload_id,
        )
        part_count = cast(int, asset["part_count"])
        if any(number > part_count for number in command.part_numbers):
            raise problem(
                status=422,
                code="LEROBOT_PART_OUT_OF_RANGE",
                title="LeRobot part number is out of range",
                detail="Part authorization must stay within the declared source object.",
            )
        key = self._key(
            organization_id,
            command.dataset_id,
            import_id,
            command.path,
        )
        completed = self._storage.head(key)
        if completed is not None:
            if completed.size != asset["size"]:
                raise problem(
                    status=409,
                    code="LEROBOT_ASSET_SIZE_MISMATCH",
                    title="LeRobot asset size mismatch",
                    detail="The completed source object no longer matches its declaration.",
                )
            return LeRobotPartGrantV1(path=command.path, parts=(), completed=True)
        uploaded_by_number = {
            item.part_number: item
            for item in self._storage.list_parts(key, command.multipart_upload_id)
        }
        uploaded_part_numbers = tuple(
            number
            for number in command.part_numbers
            if (part := uploaded_by_number.get(number)) is not None
            and part.size == lerobot_part_size(cast(int, asset["size"]), number)
        )
        missing_part_numbers = tuple(
            number for number in command.part_numbers if number not in uploaded_part_numbers
        )
        return LeRobotPartGrantV1(
            path=command.path,
            parts=self._parts(
                key,
                command.multipart_upload_id,
                missing_part_numbers,
            ),
            uploaded_part_numbers=uploaded_part_numbers,
        )

    def upload_part(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
        import_id: str,
        dataset_id: str,
        path: str,
        multipart_upload_id: str,
        part_number: int,
        body: BinaryIO,
        size: int,
    ) -> MultipartPart:
        self._authorize(
            auth,
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
        )
        session = self._require_session(
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
            dataset_id=dataset_id,
            import_id=import_id,
        )
        asset = self._require_asset(
            session,
            path=path,
            multipart_upload_id=multipart_upload_id,
        )
        try:
            expected_size = lerobot_part_size(cast(int, asset["size"]), part_number)
        except ValueError as exc:
            raise problem(
                status=422,
                code="LEROBOT_PART_OUT_OF_RANGE",
                title="LeRobot part number is out of range",
                detail="The uploaded part must stay within the declared source object.",
            ) from exc
        if size != expected_size:
            raise problem(
                status=422,
                code="LEROBOT_PART_SIZE_MISMATCH",
                title="LeRobot part size does not match",
                detail="The proxied part must match the canonical multipart plan.",
            )
        key = self._key(organization_id, dataset_id, import_id, path)
        return self._storage.upload_part_stream(
            key,
            multipart_upload_id,
            part_number,
            body,
            size,
        )

    def complete_asset(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
        import_id: str,
        command: CompleteLeRobotAssetV1,
    ) -> LeRobotAssetCompletedV1:
        self._authorize(
            auth,
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
        )
        session = self._require_session(
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
            dataset_id=command.dataset_id,
            import_id=import_id,
        )
        asset = self._require_asset(
            session,
            path=command.path,
            multipart_upload_id=command.multipart_upload_id,
        )
        if command.size != asset["size"] or command.part_count != asset["part_count"]:
            raise problem(
                status=409,
                code="LEROBOT_ASSET_DECLARATION_CHANGED",
                title="LeRobot asset declaration changed",
                detail="The completed object must match the source file selected at upload start.",
            )
        key = self._key(
            organization_id,
            command.dataset_id,
            import_id,
            command.path,
        )
        uploaded = self._storage.list_parts(key, command.multipart_upload_id)
        expected_numbers = tuple(range(1, command.part_count + 1))
        actual_numbers = tuple(item.part_number for item in uploaded)
        if (
            actual_numbers != expected_numbers
            or sum(item.size for item in uploaded) != command.size
        ):
            raise problem(
                status=409,
                code="LEROBOT_ASSET_PART_SET_INCOMPLETE",
                title="LeRobot asset upload is incomplete",
                detail="Every declared file part must be uploaded before completion.",
            )
        completed_parts = tuple(
            CompletedPart(part_number=item.part_number, etag=item.etag) for item in uploaded
        )
        metadata = self._storage.complete_multipart(
            key,
            command.multipart_upload_id,
            completed_parts,
        )
        if metadata.size != command.size:
            raise problem(
                status=422,
                code="LEROBOT_ASSET_SIZE_MISMATCH",
                title="LeRobot asset size mismatch",
                detail="The completed source object does not match its declared browser file size.",
            )
        return LeRobotAssetCompletedV1(
            path=command.path,
            size=metadata.size,
            parts=completed_parts,
        )

    def commit(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
        import_id: str,
        command: CommitLeRobotImportV1,
    ) -> LeRobotImportAcceptedV1:
        self._authorize(
            auth,
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
        )
        session = self._require_session(
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
            dataset_id=command.manifest.dataset_id,
            import_id=import_id,
        )
        if session.get("manifest") != _canonical_manifest(command.manifest):
            raise problem(
                status=409,
                code="LEROBOT_IMPORT_MANIFEST_CHANGED",
                title="LeRobot import manifest changed",
                detail=(
                    "Commit must use the exact original directory manifest accepted "
                    "at upload start."
                ),
            )
        root = self._root(organization_id, command.manifest.dataset_id, import_id)
        raw_files: list[dict[str, object]] = []
        total_size = 0
        for source in command.manifest.files:
            source_key = f"{root}/source/{source.path}"
            metadata = self._storage.head(source_key)
            if metadata is None or metadata.size != source.size:
                raise problem(
                    status=409,
                    code="LEROBOT_IMPORT_FILES_INCOMPLETE",
                    title="LeRobot import is incomplete",
                    detail=(
                        "Every selected original LeRobot source file must be complete "
                        "before commit."
                    ),
                )
            digest = hashlib.sha256()
            for chunk in self._storage.read_chunks(source_key):
                digest.update(chunk)
            raw_files.append(
                {"path": source.path, "size": source.size, "sha256": digest.hexdigest()}
            )
            total_size += source.size

        info_body = b"".join(self._storage.read_chunks(f"{root}/source/meta/info.json"))
        if len(info_body) > 1024 * 1024:
            raise problem(
                status=422,
                code="LEROBOT_INFO_TOO_LARGE",
                title="LeRobot metadata is too large",
                detail="The original meta/info.json exceeds the supported 1 MiB limit.",
            )
        try:
            uploaded_info = json.loads(info_body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise problem(
                status=422,
                code="LEROBOT_INFO_INVALID",
                title="LeRobot metadata is invalid",
                detail="The uploaded original meta/info.json is not valid UTF-8 JSON.",
            ) from exc
        if uploaded_info != command.manifest.info:
            raise problem(
                status=422,
                code="LEROBOT_INFO_CHANGED",
                title="LeRobot metadata changed",
                detail=(
                    "The uploaded original meta/info.json differs from the locally "
                    "inspected metadata."
                ),
            )
        dataset_digest = _raw_content_digest(raw_files)
        raw_manifest = {
            "schema_version": "raw-upload-manifest/v1",
            "upload_id": import_id,
            "organization_id": organization_id,
            "project_id": project_id,
            "region_code": region_code,
            "dataset_id": command.manifest.dataset_id,
            "collection_task_id": command.manifest.collection_task_id,
            "robot_id": command.manifest.robot_id,
            "source_format": "lerobot",
            "source_format_version": "v3.0",
            "content_hash": dataset_digest,
            "storage_mode": "native_objects",
            "source_prefix": f"{root}/source",
            "total_size": total_size,
            "file_count": len(raw_files),
            "files": sorted(raw_files, key=lambda item: str(item["path"])),
            "repository": f"platform-web/{dataset_digest[:32]}",
            "resolved_revision": dataset_digest[:40],
            "episode_count": command.manifest.episode_count,
        }
        # Raw source objects are immutable and byte-identical to the browser files.
        # Platform metadata is stored beside, never inside, the original source tree.
        self._storage.put_json(f"{root}/manifest.json", raw_manifest, if_none_match=False)
        episode_plan = build_import_plan(
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
            dataset_id=command.manifest.dataset_id,
            collection_task_id=command.manifest.collection_task_id,
            robot_id=command.manifest.robot_id,
            raw_upload_id=import_id,
            raw_manifest_key=f"{root}/manifest.json",
            episode_count=command.manifest.episode_count,
        )
        plan_root = (
            f"derived/lerobot-imports/{organization_id}/{command.manifest.dataset_id}/{import_id}"
        )
        for episode_task in episode_plan.episode_tasks:
            self._storage.put_json(
                f"{plan_root}/episodes/{episode_task.source.episode_index:06d}.json",
                episode_task.model_dump(mode="json"),
                if_none_match=False,
            )
        episode_plan_key = f"{plan_root}/plan.json"
        self._storage.put_json(
            episode_plan_key,
            episode_plan.model_dump(mode="json"),
            if_none_match=False,
        )
        now = utc_now()
        self.raw_sources.register_committed(
            CommittedRawSourceGraph(
                source=RawSource(
                    raw_source_id=import_id,
                    organization_id=organization_id,
                    project_id=project_id,
                    region_code=region_code,
                    upload_id=import_id,
                    dataset_id=command.manifest.dataset_id,
                    collection_task_id=command.manifest.collection_task_id,
                    robot_id=command.manifest.robot_id,
                    source_format=RawSourceFormat.LEROBOT_V3,
                    source_format_version="v3.0",
                    manifest_key=f"{root}/manifest.json",
                    storage_prefix=f"{root}/source",
                    content_hash=dataset_digest,
                    file_count=len(raw_files),
                    total_bytes=total_size,
                    created_at=now,
                    committed_at=now,
                    updated_at=now,
                ),
                episodes=tuple(
                    RawSourceEpisode(
                        organization_id=organization_id,
                        project_id=project_id,
                        region_code=region_code,
                        raw_source_id=import_id,
                        episode_id=f"lerobot-{import_id[:16]}-ep-{episode_index:06d}",
                        source_episode_index=episode_index,
                        created_at=now,
                        updated_at=now,
                    )
                    for episode_index in range(command.manifest.episode_count)
                ),
                job=RawIngestJob(
                    organization_id=organization_id,
                    project_id=project_id,
                    region_code=region_code,
                    job_id=f"lerobot-import-{import_id}",
                    raw_source_id=import_id,
                    job_type=RawIngestJobType.LEROBOT_IMPORT,
                    adapter_name="lerobot_v3",
                    created_at=now,
                    updated_at=now,
                ),
            )
        )
        return LeRobotImportAcceptedV1(
            import_id=import_id,
            episode_count=command.manifest.episode_count,
            source_file_count=len(command.manifest.files),
            episode_task_count=len(episode_plan.episode_tasks),
            episode_plan_key=episode_plan_key,
        )

    def _require_session(
        self,
        *,
        organization_id: str,
        project_id: str,
        region_code: str,
        dataset_id: str,
        import_id: str,
    ) -> dict[str, object]:
        key = f"{self._root(organization_id, dataset_id, import_id)}/upload-session.json"
        if self._storage.head(key) is None:
            raise problem(
                status=404,
                code="LEROBOT_IMPORT_NOT_FOUND",
                title="LeRobot import not found",
                detail="No upload session exists for this project and dataset scope.",
            )
        try:
            value = json.loads(b"".join(self._storage.read_chunks(key)))
        except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("stored LeRobot upload session is unreadable") from exc
        if not isinstance(value, dict) or any(
            value.get(name) != expected
            for name, expected in (
                ("organization_id", organization_id),
                ("project_id", project_id),
                ("region_code", region_code),
            )
        ):
            raise problem(
                status=404,
                code="LEROBOT_IMPORT_NOT_FOUND",
                title="LeRobot import not found",
                detail="The upload session does not belong to this request scope.",
            )
        return value

    @staticmethod
    def _require_asset(
        session: dict[str, object],
        *,
        path: str,
        multipart_upload_id: str,
    ) -> dict[str, object]:
        assets = session.get("assets")
        if isinstance(assets, list):
            for value in assets:
                if (
                    isinstance(value, dict)
                    and value.get("path") == path
                    and value.get("multipart_upload_id") == multipart_upload_id
                ):
                    return value
        raise problem(
            status=409,
            code="LEROBOT_ASSET_NOT_DECLARED",
            title="LeRobot asset was not declared",
            detail="The source object is not part of the upload session manifest.",
        )

    def _parts(
        self,
        key: str,
        upload_id: str,
        part_numbers: tuple[int, ...],
    ) -> tuple[PartAuthorization, ...]:
        expires_at = utc_now() + timedelta(seconds=self._authorization_ttl)
        return tuple(
            PartAuthorization(
                part_number=number,
                url=self._storage.presign_part(
                    key,
                    upload_id,
                    number,
                    self._authorization_ttl,
                ),
                expires_at=expires_at,
            )
            for number in part_numbers
        )

    def _key(
        self,
        organization_id: str,
        dataset_id: str,
        import_id: str,
        path: str,
    ) -> str:
        return f"{self._root(organization_id, dataset_id, import_id)}/source/{path}"

    def _root(
        self,
        organization_id: str,
        dataset_id: str,
        import_id: str,
    ) -> str:
        if len(import_id) != 32 or any(char not in "0123456789abcdef" for char in import_id):
            raise problem(
                status=404,
                code="LEROBOT_IMPORT_NOT_FOUND",
                title="LeRobot import not found",
                detail="The browser import handle is invalid for this project scope.",
            )
        return f"raw/{organization_id}/{dataset_id}/{import_id}"

    @staticmethod
    def _authorize(
        auth: AuthContext,
        *,
        organization_id: str,
        project_id: str,
        region_code: str,
    ) -> None:
        ScopeGuard.require(auth, project_id, region_code, organization_id)
        select_request_scope(project_id, region_code, organization_id=organization_id)
        capabilities = auth.effective_capabilities(project_id, organization_id)
        if not auth.is_platform_admin and "upload.manage" not in capabilities:
            raise problem(
                status=403,
                code="CAPABILITY_REQUIRED",
                title="Insufficient capability",
                detail="The current project scope cannot create LeRobot imports.",
            )


def _raw_content_digest(files: list[dict[str, object]]) -> str:
    """Hash the verified file inventory, including each object's SHA-256."""

    payload = {
        "source_format": "lerobot_v3",
        "source_format_version": "v3.0",
        "files": sorted(files, key=lambda item: str(item["path"])),
    }
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(body).hexdigest()


def _canonical_manifest(manifest: CreateLeRobotImportV1) -> dict[str, object]:
    document = manifest.model_dump(mode="json")
    files = document.get("files")
    if not isinstance(files, list):
        raise RuntimeError("validated LeRobot manifest files are unavailable")
    document["files"] = sorted(files, key=lambda item: str(item["path"]))
    return document


def _stable_import_id(
    *,
    organization_id: str,
    project_id: str,
    region_code: str,
    manifest: CreateLeRobotImportV1,
) -> str:
    """Bind one immutable browser import to one scoped Dataset on the server."""

    identity = {
        "schema_version": "lerobot-web-upload-identity/v1",
        "organization_id": organization_id,
        "project_id": project_id,
        "region_code": region_code,
        "dataset_id": manifest.dataset_id,
    }
    body = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(body).hexdigest()[:32]
