"""Read-only aggregation from a committed manifest and exact-version scoped records."""

from hashlib import sha256
from uuid import uuid4

import pyarrow.parquet as pq

from hc_data_platform.annotation.delivery_models import AnnotationDeliveryPlan
from hc_data_platform.data_formats.lerobot_layout import VIDEO_PATH

from .detail_models import (
    EpisodeAction,
    EpisodeAnnotationState,
    EpisodeCameraDetail,
    EpisodeDetail,
    EpisodeFile,
    EpisodeFilePage,
    EpisodeRevisionIdentity,
)
from .detail_state import detail_state
from .errors import PackageConflict
from .models import DataVersionRef, canonical
from .quality import read_episode_quality
from .review_models import EpisodeDataReview


def action(auth, data, name, capabilities, *, reason=None):
    permitted = all(
        auth.has_capability(c, data.identity.project_id, data.identity.organization_id)
        for c in capabilities
    )
    denied = "MISSING_CAPABILITY" if not permitted else reason
    return EpisodeAction(
        action=name,
        allowed=denied is None,
        required_capabilities=capabilities,
        denied_reason=denied,
    )


def file_id(path):
    return sha256(path.encode()).hexdigest()


def open_download(service, package, requested_id, actor_id):
    entry = next((e for e in package.manifest.files if file_id(e.path) == requested_id), None)
    if entry is None:
        raise KeyError("file ID is absent from the authorized manifest")
    # The descriptor pins the verified inode even if the bounded cache evicts its
    # pathname during HTTP streaming. No cache lock is held across a slow client.
    with package.file(entry.path) as path:
        stream = path.open("rb")
    try:
        with service.repository.transaction() as cursor:
            cursor.execute(
                """INSERT INTO core.audit_events
                (audit_id,organization_id,project_id,region_code,actor_id,action,
                 resource_type,resource_id,request_id,details,occurred_at)
                VALUES (%s,%s,%s,%s,%s,'episode.file.authorized','EPISODE_DATA_VERSION',%s,
                  NULLIF(current_setting('app.request_id',true),''),%s::jsonb,clock_timestamp())""",
                (
                    uuid4(),
                    *package.receipt.data.identity.scope,
                    actor_id,
                    package.receipt.data.data_version_id,
                    canonical(
                        {
                            "file_id": requested_id,
                            "path": entry.path,
                            "sha256": entry.sha256,
                            "content_hash": package.receipt.data.content_hash,
                        }
                    ).decode(),
                ),
            )
        return entry, stream
    except BaseException:
        stream.close()
        raise


def exact_ns(document):
    """Keep arbitrary extension ns fields lossless at the browser boundary."""
    if isinstance(document, dict):
        return {
            k: str(v) if k.endswith("_ns") and isinstance(v, int) else exact_ns(v)
            for k, v in document.items()
        }
    if isinstance(document, list):
        return [exact_ns(v) for v in document]
    return document


def file_page(package, auth, *, after=None, limit=50):
    if not 1 <= limit <= 100:
        raise ValueError("file page limit must be 1..100")
    entries = package.manifest.files
    position = 0
    if after is not None:
        matches = [i for i, e in enumerate(entries) if file_id(e.path) == after]
        if not matches:
            raise ValueError("file cursor does not belong to this data version")
        position = matches[0] + 1
    info = package.document("meta/info.json")
    videos = {
        VIDEO_PATH.format(video_key=key, chunk_index=0, file_index=0): "DEPTH"
        if f.get("info", {}).get("is_depth_map")
        else "RGB"
        for key, f in info["features"].items()
        if f["dtype"] == "video"
    }
    items = []
    for entry in entries[position : position + limit]:
        name = entry.path
        category = videos.get(name)
        if category is None:
            category = (
                "NUMERIC"
                if name.startswith("data/")
                else "ANNOTATION"
                if name.startswith("hc/annotations/") or name.startswith("hc/subtask")
                else "REPORT"
                if "report" in name or "quality" in name
                else "DEPTH"
                if name.startswith("hc/depth/")
                else "METADATA"
            )
        head = package.storage.head(f"{package.receipt.data.package_prefix}/{name}")
        status = (
            "MISSING"
            if head is None
            else "SIZE_MISMATCH"
            if head.size != entry.size
            else "AVAILABLE"
        )
        items.append(
            EpisodeFile(
                file_id=file_id(name),
                **entry.model_dump(),
                category=category,
                availability=status,
                download=action(
                    auth,
                    package.receipt.data,
                    "DOWNLOAD_FILE",
                    ("export.download",),
                    reason=None if status == "AVAILABLE" else status,
                ),
            )
        )
    return EpisodeFilePage(
        data=package.receipt.data,
        items=tuple(items),
        total=len(entries),
        next_after=items[-1].file_id if position + limit < len(entries) else None,
    )


def depth_details(package, feature, declaration):
    prefix = declaration["evidence_path"]
    profile = package.document(f"{prefix}/profile.json")
    report = package.document(f"{prefix}/report.json")
    if (
        report["profile_sha256"] != profile["profile_sha256"]
        or report["frames"] != package.manifest.frame_count
    ):
        raise PackageConflict("depth report differs from the requested package")
    dtypes, scales, maximum_error = set(), set(), None
    with package.file(f"{prefix}/frames.parquet") as path:
        for batch in pq.ParquetFile(path).iter_batches(
            batch_size=1024, columns=["source_dtype", "depth_scale_m_per_unit", "time_error_ns"]
        ):
            for row in batch.to_pylist():
                if row["source_dtype"] is not None:
                    dtypes.add(row["source_dtype"])
                if row["depth_scale_m_per_unit"] is not None:
                    scales.add(row["depth_scale_m_per_unit"])
                if row["time_error_ns"] is not None:
                    maximum_error = max(maximum_error or 0, abs(row["time_error_ns"]))
    totals, frames = report["totals"], report["frames"]
    window = report.get("window", {})
    return exact_ns(
        {
            "profile": profile,
            "output_unit": "m",
            "source_dtypes": sorted(dtypes),
            "source_scales_m_per_unit": sorted(scales),
            "source_profiles": report.get("source_profiles", {}),
            "source_frame_count": window.get("source_frames"),
            "target_frame_count": frames,
            "source_frame_count_reason": None if "source_frames" in window else "NOT_RECORDED",
            "shape": report["shape"],
            "totals": totals,
            "measurement_status": "NO_SOURCE_FRAMES"
            if totals["missing_frames"] == frames
            else "NO_VALID_PIXELS"
            if totals["valid_pixels"] == 0
            else "AVAILABLE",
            "matched_frame_ratio": (frames - totals["missing_frames"]) / frames,
            "missing_frame_ratio": totals["missing_frames"] / frames,
            "invalid_pixel_ratio": totals["invalid_pixels"] / totals["pixels"],
            "saturated_pixel_ratio": (
                totals["saturated_low_pixels"] + totals["saturated_high_pixels"]
            )
            / totals["pixels"],
            "maximum_time_error_ns": maximum_error,
            "quantization": report["quantization"],
            "video_encoding": report["video_encoding"],
            "frame_mapping_file_id": file_id(f"{prefix}/frames.parquet"),
            "mask_file_id": file_id(f"{prefix}/validity/flags.parquet"),
            "calibration": package.document("hc/calibration.json"),
            "model_depth_training_support": "NOT_VALIDATED",
        }
    )


def annotation_state(state, data, allowed, auth):
    if not allowed:
        return EpisodeAnnotationState(availability="FORBIDDEN")
    if not state["tasks"]:
        return EpisodeAnnotationState(availability="NOT_CREATED")
    if len(state["tasks"]) > 1:
        return EpisodeAnnotationState(
            availability="SELECTION_REQUIRED",
            task_options=tuple(
                {
                    k: t[k]
                    for k in (
                        "task_id",
                        "dataset_version",
                        "status",
                        "current_revision",
                        "state_version",
                    )
                }
                for t in state["tasks"][:100]
            ),
            task_options_has_more=len(state["tasks"]) > 100,
        )
    task, revision = state["tasks"][0], state["revision"]
    if DataVersionRef.model_validate(task["data_ref"]) != data:
        raise PackageConflict("annotation task differs from the requested version")
    expected = {revision["revision"]: revision["content_hash"]}
    for name in ("submission", "approval"):
        if state[name]:
            expected[state[name]["revision"]] = state[name]["revision_content_hash"]
    files = {row["revision"]: row for row in state["files"]}
    for number, content in expected.items():
        row = files.setdefault(
            number,
            {
                "task_id": task["task_id"],
                "revision": number,
                "revision_content_hash": content,
                "status": "NOT_REQUESTED",
                "generation": 0,
                "content_sha256": None,
                "size_bytes": None,
                "error_code": None,
            },
        )
        if row["revision_content_hash"] != content:
            raise PackageConflict("annotation file differs from the selected revision")
        row["download"] = action(
            auth,
            data,
            "DOWNLOAD_ANNOTATION_FILE",
            ("annotation_task.read", "export.download"),
            reason=None if row["status"] == "READY" else "FILE_NOT_READY",
        ).model_dump(mode="json")
    return EpisodeAnnotationState(
        availability="AVAILABLE",
        task_id=task["task_id"],
        status=task["status"],
        state_version=task["state_version"],
        current_revision=EpisodeRevisionIdentity.model_validate(
            {k: v for k, v in revision.items() if k != "data_ref"}
        ),
        latest_submission=state["submission"],
        latest_approval=state["approval"],
        latest_review=state["review"],
        current_submission_id=task["current_submission_id"],
        current_approved_review_id=task["approved_review_id"],
        has_unsubmitted_changes=task["current_submission_id"] is None
        or task["submitted_revision"] != task["current_revision"],
        revision_files=tuple(files[n] for n in sorted(files)),
        historical_migrations=tuple(
            {
                **migration,
                "download": action(
                    auth,
                    data,
                    "DOWNLOAD_ANNOTATION_MIGRATION",
                    ("annotation_task.read", "export.download"),
                ).model_dump(mode="json"),
            }
            for migration in state.get("migrations", [])
        ),
    )


def read_detail(service, package, auth, *, delivery_id=None, annotation_task_id=None):
    data, frozen = package.receipt.data, package.manifest.input
    allowed = auth.has_capability(
        "annotation_task.read", data.identity.project_id, data.identity.organization_id
    )
    if (delivery_id is not None or annotation_task_id is not None) and not allowed:
        raise PermissionError("selected annotation delivery requires annotation_task.read")
    state = detail_state(
        service.repository,
        data,
        annotation_allowed=allowed,
        delivery_id=delivery_id,
        annotation_task_id=annotation_task_id,
    )
    annotation = annotation_state(state, data, allowed, auth)
    if delivery_id is not None and annotation.availability == "SELECTION_REQUIRED":
        raise ValueError("select an annotation task before choosing its subtask delivery")
    review = (
        EpisodeDataReview.model_validate(state["data_review"]) if state["data_review"] else None
    )
    if review is not None and review.data != data:
        raise PackageConflict("data review differs from the fixed data version")
    info = package.document("meta/info.json")
    origin = package.document("hc/source.json")
    modalities = package.document("hc/modalities.json")
    with package.file("meta/tasks.parquet") as path:
        instructions = pq.read_table(path, columns=["task"]).column("task").to_pylist()
    cameras = []
    for feature, camera in info["features"].items():
        if camera["dtype"] != "video":
            continue
        is_depth = camera.get("info", {}).get("is_depth_map", False)
        row = service.previews._row(data, feature)
        status, reason = (row[0], None) if row else ("NOT_GENERATED", "PREVIEW_NOT_GENERATED")
        if status == "READY":
            try:
                _, _, _, source_hash = service.previews._source(package, feature)
                service.previews._verified(package, feature, row, source_hash, verify_bytes=False)
            except (PackageConflict, ValueError, KeyError):
                status, reason = "INTEGRITY_FAILED", "PREVIEW_INTEGRITY_FAILED"
        cameras.append(
            EpisodeCameraDetail(
                feature=feature,
                kind="DEPTH" if is_depth else "RGB",
                shape=tuple(camera["shape"]),
                video_file_id=file_id(
                    VIDEO_PATH.format(video_key=feature, chunk_index=0, file_index=0)
                ),
                preview_status=status,
                preview_reason=reason,
                depth=depth_details(package, feature, modalities["depth"][feature])
                if is_depth
                else None,
            )
        )
    deliveries = []
    for job in state["jobs"][:20]:
        plan = AnnotationDeliveryPlan.model_validate(job["plan"])
        if plan.binding.data != data or plan.job_id != job["job_id"]:
            raise PackageConflict("delivery differs from the fixed data version")
        deliveries.append(
            {k: v for k, v in job.items() if k != "plan"}
            | {
                "task_id": plan.binding.task_id,
                "revision": plan.binding.revision,
                "revision_content_hash": plan.binding.revision_content_hash,
                "track": plan.binding.track.model_dump(mode="json"),
                "review": plan.review.model_dump(mode="json"),
                "segment_count": len(plan.binding.track.annotation_ids),
            }
        )
    if delivery_id is not None and not deliveries:
        raise KeyError("selected delivery does not belong to this data version")
    selected = deliveries[0] if delivery_id else None
    source = {
        "document": exact_ns(origin),
        "parent_recording_id": origin.get("recording_id"),
        "parent_duration_ns": str(origin["parent_duration_ns"])
        if origin.get("parent_duration_ns") is not None
        else None,
        "parent_duration_reason": None
        if origin.get("parent_duration_ns") is not None
        else "NOT_RECORDED",
        "window": {
            k: str(origin[k])
            for k in ("start_offset_ns", "end_offset_ns", "start_ns", "end_ns")
            if k in origin
        },
        "window_clock": "recording_relative"
        if "start_offset_ns" in origin
        else origin.get("alignment_clock", "NOT_RECORDED"),
        "frame_mapping_files": [
            file_id(e.path)
            for e in package.manifest.files
            if e.path.endswith("frames.parquet") or e.path.endswith("source_frames.jsonl")
        ],
    }
    files = file_page(package, auth)
    missing_files = any(item.availability != "AVAILABLE" for item in files.items)
    delivery_reason = "NO_CURRENT_APPROVED_REVISION"
    annotation_reason = None if annotation.task_id else "ANNOTATION_NOT_CREATED"
    if annotation.availability == "SELECTION_REQUIRED":
        delivery_reason = annotation_reason = "ANNOTATION_SELECTION_REQUIRED"
    if annotation.current_approved_review_id and annotation.latest_approval:
        delivery_reason = (
            None
            if any(
                f["revision"] == annotation.latest_approval["revision"] and f["status"] == "READY"
                for f in annotation.revision_files
            )
            else "ANNOTATION_FILE_NOT_READY"
        )
    return EpisodeDetail(
        data=data,
        display_name=origin.get("display_name") or data.identity.episode_id,
        display_name_origin="SOURCE" if origin.get("display_name") else "EPISODE_ID_FALLBACK",
        task={
            "task_id": frozen.task_id,
            "instructions": instructions,
            "requirements": origin.get("task_requirements"),
            "requirements_status": "RECORDED"
            if origin.get("task_requirements") is not None
            else "NOT_RECORDED",
        },
        robot_id=frozen.robot_id,
        recording_date=frozen.recording_date,
        duration_seconds=info.get("hc.duration_seconds", data.frame_count / info["fps"]),
        frequency_hz=info["fps"],
        camera_stream_count=len(cameras),
        physical_camera_count=None,
        physical_camera_count_reason="PHYSICAL_CAMERA_PAIRING_NOT_RECORDED",
        source=source,
        quality=read_episode_quality(package),
        data_review_status=review.decision if review else "NOT_REVIEWED",
        latest_data_review=review,
        annotation=annotation,
        subtasks={
            "status": "SELECTED" if selected else "NOT_SPECIFIED",
            "delivery_id": delivery_id,
            "revision": selected["revision"] if selected else None,
            "track": selected["track"] if selected else None,
            "segment_count": selected["segment_count"] if selected else None,
        },
        cameras=tuple(cameras),
        files=files,
        deliveries=tuple(deliveries),
        deliveries_has_more=len(state["jobs"]) > 20,
        allowed_actions=tuple(
            action(auth, data, name, caps, reason=reason)
            for name, caps, reason in (
                ("READ_FILES", ("dataset_version.read",), None),
                (
                    "DOWNLOAD_COMPLETE",
                    ("export.download",),
                    "PACKAGE_FILES_UNAVAILABLE" if missing_files else None,
                ),
                ("SUBMIT_DATA_REVIEW", ("dataset_version.review",), None),
                (
                    "OPEN_ANNOTATION",
                    ("annotation_task.read",),
                    annotation_reason,
                ),
                (
                    "CREATE_ANNOTATED_DELIVERY",
                    ("annotation_task.read", "export.create", "export.download"),
                    delivery_reason,
                ),
            )
        ),
        observed_at=state["observed_at"],
    )
