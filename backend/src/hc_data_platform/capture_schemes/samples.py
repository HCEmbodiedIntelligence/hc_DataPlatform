"""Scoped, checksum-verified sample decoding; publication never accepts a client pass flag."""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from typing import Any

from hc_data_platform.continuous_recordings.mcap_contracts import McapProcessingConfig
from hc_data_platform.continuous_recordings.mcap_native import inspect, prepare, resolve
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.ingest.raw_sources import RawSourceRepositoryPort
from hc_data_platform.lerobot_imports.reader import required_episode_files
from hc_data_platform.lerobot_imports.source_browser import OriginalSourceBrowser
from hc_data_platform.object_storage.ports import BrowsableObjectReader
from hc_data_platform.robot_ingest.raw_recording import verify

from .common import Document, digest, reject
from .contracts import effective_adapter
from .draft_frequency import mcap_frequency, with_frequency
from .models import DeviceMapping, OutputContract, Sample
from .processing import validate_lerobot, validate_mcap_output


class StoredSampleValidator:
    def __init__(
        self, storage: BrowsableObjectReader, raw_sources: RawSourceRepositoryPort
    ) -> None:
        self.storage = storage
        self.raw_sources = raw_sources

    def validate(
        self,
        organization_id: str,
        document: Document,
        sample: Sample,
        mapping: DeviceMapping | None = None,
    ) -> Document:
        return validate_sample(self, organization_id, document, sample, mapping)


def localize(
    native: StoredSampleValidator, raw: Any, manifest: Document, root: Path, sample: Sample
) -> tuple[int, ...]:
    files = {item["path"]: item for item in manifest["files"]}

    def copy(relative: str) -> Path:
        path = PurePosixPath(relative)
        if (
            path.is_absolute()
            or ".." in path.parts
            or "\\" in relative
            or path.as_posix() != relative
        ):
            raise ValueError("unsafe sample path")
        descriptor = files.get(relative)
        if descriptor is None:
            raise ValueError("sample file is not present in its committed manifest")
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("wb") as stream:
            verify(  # type: ignore[no-untyped-call]  # Existing checksum verifier boundary.
                native.storage,
                descriptor.get("object_key") or raw.storage_prefix + "/" + relative,
                descriptor["size"],
                descriptor["sha256"],
                lambda: None,
                stream,
            )
        return target

    if sample.source_format == "CAPTURE_BUNDLE":
        for relative in sorted(files):
            copy(relative)
        return ()
    if sample.source_format == "MCAP":
        if not sample.source_path.lower().endswith(".mcap"):
            raise ValueError("MCAP sample must reference an MCAP file")
        copy(sample.source_path)
        return ()
    import pyarrow.parquet as pq

    info = json.loads(copy("meta/info.json").read_text())
    rows = []
    for relative in sorted(files):
        if relative.startswith("meta/episodes/") and relative.endswith(".parquet"):
            rows.extend(pq.read_table(copy(relative)).to_pylist())
    indices = tuple(row["episode_index"] for row in rows)
    if (
        sample.episode_index not in indices
        or len(set(indices)) != len(indices)
        or len(indices) != info["total_episodes"]
        or any(type(index) is not int or index < 0 for index in indices)
    ):
        raise ValueError("sample episodes must all be uniquely declared in metadata")
    required = {"meta/tasks.parquet"}
    for row in rows:
        required.update(required_episode_files(info, row))
    for relative in sorted(required):
        copy(relative)
    for relative in files:
        if relative in {
            "capture-context.json",
            "export-complete.json",
            "source_mapping.jsonl",
            "hc/calibration.json",
            "hc/modalities.json",
        } or relative.startswith("hc/depth/"):
            copy(relative)
    return indices


def validate_sample(
    native: StoredSampleValidator,
    org: str,
    document: Document,
    sample: Sample,
    mapping: DeviceMapping | None = None,
) -> Document:
    browser = OriginalSourceBrowser(native.storage, native.raw_sources)
    raw = browser.source(org, sample.project_id, sample.region_code, sample.raw_source_id)
    if raw.source_format.value != sample.source_format:
        reject("CAPTURE_FORMAT_UNSUPPORTED", "样本格式与所选适配器不一致")
    adapter = effective_adapter(document, sample.source_format, mapping or DeviceMapping())
    evidence: Document = {
        "source_format": sample.source_format,
        "parser_version": adapter["parser_version"],
        "sample": sample.model_dump(mode="json"),
        "sample_robot_id": raw.robot_id,
        "sample_sha256": raw.content_hash,
        "draft_sha256": digest(document),
        "status": "FAILED",
        "differences": [],
        "presence": {},
    }
    try:
        with TemporaryDirectory(prefix="capture-scheme-sample-") as temporary:
            root = Path(temporary)
            manifest = browser.manifest(raw)
            indices = localize(native, raw, manifest, root, sample)
            if sample.source_format == "LEROBOT_V3":
                info = json.loads((root / "meta/info.json").read_text())
                resolved = with_frequency(document, info["fps"])
                output = OutputContract.model_validate(resolved["output"])
                episodes = []
                for index in indices:
                    result = validate_lerobot(root, index, output, adapter)
                    episodes.append({"episode_index": index, **result})
                    if index == sample.episode_index:
                        evidence.update(result)
                evidence["validated_episodes"] = episodes
            elif sample.source_format == "CAPTURE_BUNDLE":
                session = json.loads((root / "session.json").read_text())
                if session.get("schema_version") == "hc-topic-video/v1":
                    from hc_data_platform.continuous_recordings.topic_video import restore

                    path = restore(root, root / "decoded-topic-video.mcap")
                    frequency = document["output"].get("frequency_hz") or adapter[
                        "configuration"
                    ].get("fps")
                    if frequency is None:
                        frequency = mcap_frequency(path, adapter["configuration"])
                    resolved = with_frequency(document, frequency)
                    cfg = McapProcessingConfig.model_validate(
                        effective_adapter(
                            resolved, sample.source_format, mapping or DeviceMapping()
                        )["configuration"]
                    )
                    _, decoded = prepare(path, root / "decoded", cfg, preserve_source=True)
                    evidence.update(
                        validate_mcap_output(
                            OutputContract.model_validate(resolved["output"]), cfg, decoded
                        ),
                        frame_count=decoded["frames"],
                        frequency_hz=decoded["config"]["fps"],
                    )
                else:
                    from .bundles import decode_sample

                    resolved = with_frequency(document, session["config"]["dataset"]["fps"])
                    evidence.update(
                        decode_sample(
                            root,
                            root / "decoded",
                            effective_adapter(
                                resolved, sample.source_format, mapping or DeviceMapping()
                            ),
                            OutputContract.model_validate(resolved["output"]),
                        )
                    )
            else:
                path = root / sample.source_path
                inventory = inspect(path)  # type: ignore[no-untyped-call]
                frequency = document["output"].get("frequency_hz")
                if frequency is None:
                    frequency = adapter["configuration"].get("fps")
                if frequency is None:
                    frequency = mcap_frequency(path, adapter["configuration"])
                resolved = with_frequency(document, frequency)
                output = OutputContract.model_validate(resolved["output"])
                adapter = effective_adapter(
                    resolved, sample.source_format, mapping or DeviceMapping()
                )
                cfg = McapProcessingConfig.model_validate(adapter["configuration"])
                for role in ("state", "action"):
                    names, units = cfg.layout(role)
                    if (
                        not names
                        or len(units) != len(names)
                        or any(unit.strip().lower() in {"", "unknown", "未知"} for unit in units)
                    ):
                        reject("CAPTURE_STRUCTURE_MISMATCH", "状态与动作必须分别明确名称和单位")
                if cfg.derive_action_from_next_state:
                    reject("CAPTURE_FORMAT_UNSUPPORTED", "发布方案需要明确的动作来源")
                if cfg.depth_scale_m_per_unit is not None and not cfg.depth_unit_evidence:
                    reject("CAPTURE_STRUCTURE_MISMATCH", "深度单位换算必须有明确依据")
                cfg = resolve(cfg, inventory)  # type: ignore[no-untyped-call]
                _, decoded = prepare(path, root / "decoded", cfg, preserve_source=True)  # type: ignore[no-untyped-call]
                evidence.update(
                    inventory=inventory,
                    frame_count=decoded["frames"],
                    frequency_hz=decoded["config"]["fps"],
                )
                evidence.update(validate_mcap_output(output, cfg, decoded))
            evidence["status"] = "FAILED" if evidence["differences"] else "PASSED"
            if evidence["differences"]:
                evidence["error_code"] = "CAPTURE_STRUCTURE_MISMATCH"
    except ProblemException as exc:
        if not exc.problem.code.startswith("CAPTURE_") and exc.problem.code != "FILE_HASH_MISMATCH":
            raise
        evidence.update(
            status="FAILED",
            error_code=(
                "CAPTURE_SOURCE_INVALID"
                if exc.problem.code == "FILE_HASH_MISMATCH"
                else exc.problem.code
            ),
            error_detail=exc.problem.detail,
            differences=exc.problem.details.get("differences", []),
        )
    except (ValueError, OSError, KeyError) as exc:
        evidence.update(
            status="FAILED",
            error_code=(
                "CAPTURE_STRUCTURE_MISMATCH"
                if str(exc).startswith(("MCAP_MAPPING_", "MCAP_JOINT_LAYOUT_", "MCAP_REQUIRED_"))
                else "CAPTURE_SOURCE_INVALID"
            ),
            error_detail=str(exc),
        )
    return evidence
