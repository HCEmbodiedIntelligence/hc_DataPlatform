"""Reimport the lossless Episode MCAP profile into the common recording pipeline."""

import json
import math
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

import av
import pyarrow.parquet as pq
from mcap.writer import Writer

from hc_data_platform.data_formats.lerobot_layout import DATA_PATH, VIDEO_PATH
from hc_data_platform.episode_store.mcap_export import extract_episode
from hc_data_platform.episode_store.models import canonical

VERSION = "native-mcap-package/1"


def prepare_transport(path, destination, progress=lambda: None):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    with TemporaryDirectory(dir=destination.parent) as temp:
        package = Path(temp) / "episode"
        extract_episode(path, package)
        info = json.loads((package / "meta/info.json").read_bytes())
        modalities = json.loads((package / "hc/modalities.json").read_bytes())
        features = info["features"]
        cameras, media = [], {}
        duration_ns = 0
        for key, feature in features.items():
            if feature["dtype"] != "video":
                continue
            progress()
            depth = feature.get("info", {}).get("is_depth_map", False)
            camera = f"camera_{len(cameras):03d}" + ("_depth" if depth else "")
            relative = f"videos/{camera}.mp4"
            target = destination / relative
            target.parent.mkdir(exist_ok=True)
            source = package / VIDEO_PATH.format(video_key=key, chunk_index=0, file_index=0)
            shutil.copyfile(source, target)
            with av.open(str(target)) as container:
                video = container.streams.video[0]
                fps = float(video.average_rate)
                camera_duration = int(video.duration * video.time_base * 1e9)
                camera_config = {
                    "camera_id": camera,
                    "topic": key,
                    "fps": fps,
                    "height": video.height,
                    "width": video.width,
                    "codec": video.codec_context.name,
                    "clock_domain": "media",
                    "time_base_numerator": video.time_base.numerator,
                    "time_base_denominator": video.time_base.denominator,
                    "capture_start_offset_ns": "0",
                }
            duration_ns = max(duration_ns, camera_duration)
            cameras.append(camera_config)
            media[camera] = {"kind": "depth" if depth else "rgb", "video_path": relative}
            if depth:
                evidence = modalities["depth"][key]["evidence_path"]
                new_evidence = "hc/depth/" + camera.removesuffix("_depth")
                shutil.copytree(package / evidence, destination / new_evidence)
                media[camera]["evidence_path"] = new_evidence
        if not cameras:
            raise ValueError("MCAP Episode requires video for recording visualization")
        topics = [k for k, v in features.items() if v["dtype"] != "video"]
        # Canonical numeric topics retain names and units declared in the source feature.
        mapping = {
            k: "/observation/state"
            if k == "observation.state"
            else "/action"
            if k == "action"
            else k
            for k in topics
        }
        (destination / "sensors").mkdir()
        with (destination / "sensors/robot.mcap").open("wb") as output:
            writer = Writer(output)
            writer.start(profile=VERSION)
            schema = writer.register_schema("hc-feature", "jsonschema", b"{}")
            channels = {k: writer.register_channel(mapping[k], "json", schema) for k in topics}
            for batch in pq.ParquetFile(package / DATA_PATH).iter_batches(batch_size=128):
                for row in batch.to_pylist():
                    progress()
                    # Preserve the media location after quality filtering removed invalid rows.
                    stamp = round(row.get("hc.video_timestamp", row["timestamp"]) * 1e9)
                    for key in topics:
                        value = row[key]
                        if key in {"action", "observation.state"}:
                            names = features[key].get("names")
                            if isinstance(names, dict):
                                names = next(iter(names.values()))
                            if not isinstance(value, list):
                                value = [value]
                            value = {
                                "values": value,
                                "names": names or [f"joint_{i}" for i in range(len(value))],
                                "units": features[key].get("units") or ["unknown"] * len(value),
                            }
                        writer.add_message(
                            channels[key], stamp, canonical(value), stamp, row["frame_index"]
                        )
            writer.finish()
        config = {
            "schema_version": "recording-config/v1",
            "recorder_version": VERSION,
            "primary_clock_domain": "media",
            "cameras": cameras,
            "sensors": [
                {
                    "topic": mapping[k],
                    "clock_domain": "media",
                    "timestamp_mode": "RECORDING_OFFSET_NS",
                    "required": k in {"action", "observation.state"},
                }
                for k in topics
            ],
        }
        provenance = {
            "format": VERSION,
            "source_origin_ns": 0,
            "duration_ns": math.ceil(duration_ns / 1000) * 1000,
            "source_features": features,
            "source_profile": "hc-episode-mcap/1",
            "clock": "media",
        }
        for name, document in (
            ("recording-config.json", config),
            ("recording-media.json", {"format": "hc-recording-media/1", "cameras": media}),
            ("mcap-provenance.json", provenance),
        ):
            (destination / name).write_bytes(canonical(document))
        return config, provenance
