"""Native Episode packaging from a pinned, hash-verified Raw cache session."""

import hashlib
import json

from hc_data_platform.core.errors import problem
from hc_data_platform.data_formats.feature_names import feature_names
from hc_data_platform.episode_store.bundle_models import FrozenObject
from hc_data_platform.episode_store.models import EpisodeIdentity, FileEntry, PackageInput
from hc_data_platform.episode_store.native_plan import NativeEpisodePlan, NativeFile, NativeVideo
from hc_data_platform.episode_store.prepared_assets import PreparedAssets, PreparedVideo
from hc_data_platform.lance_catalog.models import DatasetSchemaSnapshot

from .local_files import local_file, safe_relative


def raw_files(raw, manifest):
    return {
        item["path"]: NativeFile(
            relative_path=item["path"],
            object=FrozenObject(
                item.get("object_key") or f"{raw.storage_prefix}/{item['path']}",
                item["size"],
                item["sha256"],
            ),
        )
        for item in manifest["files"]
        # Package manifests cannot include themselves. They remain part of Raw,
        # and are not assets copied into a newly generated package.
        if item["path"] not in {"hc/manifest.json", "hc/commit.json"}
    }


def freeze_native_plan(
    session, request, manifest, staging, quality, *, quality_evidence=None, schemas=None
):
    root, stream = session.root, session.stream
    files = raw_files(session.raw, session.raw_manifest)
    info = stream.layout.info
    metadata = {
        "frequency_hz": request.profile.frequency_hz,
        "robot_type": info.get("robot_type", "unknown"),
        "source": {
            "format": "lerobot_v3",
            "original_inventory": [
                NativeFile(
                    relative_path=item["path"],
                    object=FrozenObject(
                        item.get("object_key") or f"{session.raw.storage_prefix}/{item['path']}",
                        item["size"],
                        item["sha256"],
                    ),
                ).model_dump(mode="json")
                for item in session.raw_manifest["files"]
            ],
            "raw_upload_id": session.raw.raw_source_id,
            "raw_manifest_sha256": session.source.source_sha256,
            "episode_index": session.source.lerobot.episode_index,
        },
        "quality": quality.model_dump(mode="json"),
        **({"quality_rules": quality_evidence.model_dump(mode="json")} if quality_evidence else {}),
        "task": stream.layout.episode_metadata["tasks"][0],
    }
    for key in ("calibration", "modalities"):
        relative = f"hc/{key}.json"
        if relative in files:
            metadata[key] = json.loads(local_file(root, relative).read_bytes())
    videos = {}
    mapping = feature_names(sorted(stream.profile.topics))
    camera_mapping = {camera.topic: mapping[camera.topic] for camera in stream.profile.cameras}
    for camera in stream.profile.cameras:
        feature = info["features"][camera.feature_key]
        slice_ = stream.layout.videos[camera.feature_key]
        evidence = {}
        if feature.get("info", {}).get("is_depth_map"):
            # A root HC contract describes an independent Episode. Multi-Episode
            # containers require explicit per-Episode evidence and window mapping.
            if info["total_episodes"] != 1:
                raise problem(
                    status=422,
                    code="LEROBOT_DEPTH_EPISODE_MAPPING_REQUIRED",
                    title="需要逐 Episode 的深度来源证据",
                    detail="多 Episode 源需要明确的深度 profile、掩码和帧映射；原始上传已保留。",
                    retryable=False,
                )
            relative = (
                metadata.get("modalities", {})
                .get("depth", {})
                .get(camera.feature_key, {})
                .get("evidence_path")
            )
            if relative is None:
                raise problem(
                    status=422,
                    code="LEROBOT_DEPTH_EVIDENCE_REQUIRED",
                    title="深度来源证据不完整",
                    detail="源深度缺少 HC profile、掩码或源帧映射；原始上传已保留。",
                    retryable=False,
                )
            prefix = safe_relative(relative) + "/"
            evidence = {
                name[len(prefix) :]: file for name, file in files.items() if name.startswith(prefix)
            }
            if (
                not {"profile.json", "frames.parquet", "report.json", "validity/flags.parquet"}
                <= evidence.keys()
            ):
                raise problem(
                    status=422,
                    code="LEROBOT_DEPTH_EVIDENCE_REQUIRED",
                    title="深度来源证据不完整",
                    detail="深度声明引用的 profile、掩码、源帧映射或误差报告不在原始文件清单中。",
                    retryable=False,
                )
        videos[mapping[camera.topic]] = NativeVideo(
            source=files[slice_.source_relative_path], feature=feature, evidence=evidence
        )
    identity = EpisodeIdentity(
        organization_id=request.organization_id,
        project_id=request.project_id,
        region_code=request.region_code,
        dataset_id=request.dataset_id,
        # Stable across processing attempts and compatible with existing viewer IDs.
        episode_id="episode_" + hashlib.sha256(session.source.rollout_id.encode()).hexdigest()[:32],
        rollout_id=session.source.rollout_id,
    )
    if session.source.lerobot.capture_snapshot is not None:
        from hc_data_platform.capture_schemes.schema import semantic_schema

        capture = session.source.lerobot.capture_snapshot
        schema = semantic_schema(
            request.project_id,
            request.dataset_id,
            capture["output"],
            {topic: "json" for topic in stream.profile.topics},
            frequency_hz=request.profile.frequency_hz,
        )
        if schemas is not None:
            schema = schemas.resolve_capture_schema(schema)
        metadata["capture"] = capture
        metadata["capture_validation"] = session.source.lerobot.capture_validation
    else:
        schema = DatasetSchemaSnapshot.create(
            project_id=request.project_id,
            dataset_id=request.dataset_id,
            schema_snapshot_id=request.schema_snapshot_id,
            frequency_hz=request.profile.frequency_hz,
            fields={topic: "json" for topic in stream.profile.topics},
        )
    plan = NativeEpisodePlan.freeze(
        input=PackageInput(
            identity=identity,
            source_sha256=session.source.source_sha256,
            processor_version=request.profile.converter_version,
            configuration_sha256=(session.source.lerobot.capture_snapshot or {}).get(
                "snapshot_sha256", "0" * 64
            ),
            schema_snapshot_id=request.schema_snapshot_id,
            required_features=tuple(sorted(mapping.values())),
            required_depth_features=tuple(
                sorted(
                    key
                    for key, video in videos.items()
                    if video.feature.get("info", {}).get("is_depth_map")
                )
            ),
            robot_id=session.raw.robot_id or "unknown",
            task_id=session.raw.collection_task_id or "unknown",
        ),
        schema_snapshot=schema,
        metadata=metadata,
        alignment_sha256=staging.content_sha256,
        alignment_version=staging.alignment_version,
        alignment_profile=request.profile,
        frame_count=manifest.row_count,
        feature_mapping=camera_mapping,
        videos=videos,
    )
    try:
        plan.validate_bundle(request, manifest, staging, ())
    except ValueError as exc:
        if str(exc).startswith("LEROBOT_VIDEO_WINDOW_REQUIRED"):
            raise problem(
                status=422,
                code="LEROBOT_VIDEO_WINDOW_REQUIRED",
                title="视频需要按 Episode 窗口生成",
                detail="源视频包含共享片段或不同时间网格，当前直接复用条件不满足；原始上传已保留。",
                retryable=False,
            ) from exc
        raise
    return plan


def prepared_native_assets(session, plan, attempt_id):
    # Recheck each descriptor against the freshly verified committed manifest.
    # The plan is internal, but never treat an arbitrary caller locator as authority.
    type(plan).model_validate(plan.model_dump(mode="json"))
    if (
        session.source.source_sha256 != plan.input.source_sha256
        or session.source.rollout_id != plan.input.identity.rollout_id
        or (session.raw.organization_id, session.raw.project_id, session.raw.region_code)
        != plan.input.identity.scope
    ):
        raise PermissionError("native asset source differs from the frozen Episode")
    files = raw_files(session.raw, session.raw_manifest)

    def checked(file):
        if files.get(file.relative_path) != file:
            raise PermissionError("native asset is absent from the committed Raw manifest")
        return local_file(session.root, file.relative_path)

    videos = {}
    for target, video in plan.videos.items():
        entries = []
        for relative, file in video.evidence.items():
            path = checked(file)
            # Each camera contract must use one common evidence directory.
            expected = session.root / file.relative_path.removesuffix(relative)
            if expected / relative != path:
                raise ValueError("native depth evidence has an inconsistent relative path")
            entries.append(
                FileEntry(
                    path=relative,
                    size=file.object.size,
                    sha256=file.object.sha256,
                    media_type="application/octet-stream",
                )
            )
        roots = {
            session.root / file.relative_path.removesuffix(relative)
            for relative, file in video.evidence.items()
        }
        if len(roots) > 1:
            raise ValueError("native depth evidence requires one source directory")
        videos[target] = PreparedVideo(
            checked(video.source),
            video.source.object.sha256,
            video.source.object.size,
            video.feature,
            next(iter(roots), None),
            tuple(entries),
        )
    return PreparedAssets(plan.input.identity, attempt_id, videos)
