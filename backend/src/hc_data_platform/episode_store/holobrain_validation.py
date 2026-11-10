"""Full readback validation of the versioned 19-axis training package."""

import hashlib
import json
from collections import Counter
from pathlib import Path

import numpy as np
import pyarrow as pa

from hc_data_platform.continuous_recordings.joint_normalization import HOLOBRAIN_JOINTS

from .holobrain_schema import FEATURES, PROFILE, schema


def validate(root, checkpoint=lambda: None):
    import duckdb
    from .holobrain_export import image_bytes

    root = Path(root)
    state = json.loads((root / "state.json").read_bytes())
    info = json.loads((root / "dataset_info.json").read_bytes())
    conversion = json.loads((root / "conversion_report.json").read_bytes())
    reference_report = set(conversion) == {"episode_count", "episodes", "total_frames"}
    if (state["robo_orchard_state"]["dataset_format_version"] != "0.1.0"
            or info["features"] != FEATURES
            or (not reference_report and conversion["target_profile"] != PROFILE)):
        raise ValueError("HOLOBRAIN_METADATA_MISMATCH")
    counts, starts, lengths = Counter(), {}, []
    source_positions = {}
    total = 0
    db = duckdb.connect(str(root / "meta_db.duckdb"), read_only=True)
    try:
        tasks = {r[0] for r in db.execute('SELECT "index" FROM task').fetchall()}
        instructions = {r[0] for r in db.execute('SELECT "index" FROM instruction').fetchall()}
        with (root / "alignment_provenance.jsonl").open() as traces:
            for entry in state["_data_files"]:
                shard_count = 0
                with pa.memory_map(str(root / entry["filename"]), "r") as stream:
                    reader = pa.ipc.open_stream(stream)
                    if not reader.schema.equals(schema(), check_metadata=True):
                        raise ValueError("HOLOBRAIN_ARROW_SCHEMA_MISMATCH")
                    for batch in reader:
                        for row in batch.to_pylist():
                            checkpoint()
                            trace = json.loads(next(traces))
                            episode = row["episode_index"]
                            if (row["index"] != total or row["frame_index"] != counts[episode]
                                    or trace["index"] != total or trace["episode_index"] != episode
                                    or trace["frame_index"] != row["frame_index"]):
                                raise ValueError("HOLOBRAIN_INDEX_MISMATCH")
                            previous = source_positions.get(trace["source_index"])
                            parent_index, rgb_stamp = trace["parent_frame_index"], trace["rgb_time_ns"]
                            if (type(parent_index) is not int or parent_index < 0
                                    or (previous is not None
                                        and (parent_index <= previous[0] or rgb_stamp <= previous[1]))):
                                raise ValueError("HOLOBRAIN_SOURCE_FRAME_MAP_INVALID")
                            if row["head"]["timestamps"] != [rgb_stamp]:
                                raise ValueError("HOLOBRAIN_SOURCE_TIMESTAMP_MISMATCH")
                            source_positions[trace["source_index"]] = (parent_index, rgb_stamp)
                            if row["task_index"] not in tasks or row["instruction_index"] not in instructions or row["robot_index"] != 0:
                                raise ValueError("HOLOBRAIN_METADATA_REFERENCE_INVALID")
                            for key in ("joints", "actions"):
                                if row[key]["names"] != list(HOLOBRAIN_JOINTS):
                                    raise ValueError("HOLOBRAIN_JOINT_ORDER_INVALID")
                                for field in ("position", "velocity", "effort"):
                                    t = row[key][field]
                                    if t["shape"] != [1, 19] or len(t["data"]) != 19 or not np.isfinite(t["data"]).all():
                                        raise ValueError("HOLOBRAIN_TENSOR_INVALID")
                                if len(row[key]["timestamps"]) != 1:
                                    raise ValueError("HOLOBRAIN_TIMESTAMP_INVALID")
                            for joint_index in (7, 15):
                                if row["joints"]["position"]["data"][joint_index] != row["actions"]["position"]["data"][joint_index]:
                                    raise ValueError("HOLOBRAIN_HAND_PROXY_MISMATCH")
                            for key in ("head", "head_depth"):
                                picture = row[key]
                                body = image_bytes({"data": picture["sensor_data"][0]}, depth=key == "head_depth")
                                if hashlib.sha256(body).hexdigest() != trace["image_sha256"][key]:
                                    raise ValueError("HOLOBRAIN_IMAGE_PROVENANCE_MISMATCH")
                                if picture["image_shape"] != [400, 640]:
                                    raise ValueError("HOLOBRAIN_CAMERA_SIZE_INVALID")
                            stamps = row["head"]["timestamps"] + row["head_depth"]["timestamps"]
                            if row["timestamp_min"] != min(stamps) or row["timestamp_max"] != max(stamps):
                                raise ValueError("HOLOBRAIN_TIMESTAMP_RANGE_INVALID")
                            starts.setdefault(episode, total)
                            counts[episode] += 1
                            shard_count += 1
                            total += 1
                lengths.append(shard_count)
            if next(traces, None) is not None:
                raise ValueError("HOLOBRAIN_EXTRA_PROVENANCE_ROWS")
        episodes = db.execute('SELECT "index",dataset_begin_index,frame_num FROM episode ORDER BY "index"').fetchall()
        if episodes != [(i, starts[i], counts[i]) for i in sorted(counts)]:
            raise ValueError("HOLOBRAIN_DATABASE_FRAME_MISMATCH")
        split = info["splits"]["train"]
        reported_frames = conversion["total_frames"] if reference_report else conversion["frames"]
        if split["num_examples"] != total or split["shard_lengths"] != lengths or reported_frames != total:
            raise ValueError("HOLOBRAIN_SHARD_LENGTH_MISMATCH")
        if reference_report:
            report_episodes = conversion["episodes"]
            if (conversion["episode_count"] != len(episodes)
                    or len(report_episodes) != len(episodes)
                    or [e["frames"]["output_frames"] for e in report_episodes]
                    != [counts[i] for i in sorted(counts)]):
                raise ValueError("HOLOBRAIN_REPORT_EPISODE_MISMATCH")
            for entry in report_episodes:
                frames = entry["frames"]
                if (frames["candidate_rgb_frames"] != frames["output_frames"] + frames["dropped_frames"]
                        or sum(frames["dropped_by_reason"].values()) != frames["dropped_frames"]):
                    raise ValueError("HOLOBRAIN_REPORT_FRAME_MISMATCH")
    finally:
        db.close()
    return {"status": "PASSED", "profile": PROFILE, "frames": total, "episodes": len(counts),
            "all_arrow_rows": True, "all_images_decoded": True, "schema_metadata": True,
            "database_indexes": True, "provenance_indexes": True, "image_bytes_provenance": True,
            "training_integration": "NOT_RUN_BY_STORAGE_VALIDATOR",
            "accepted_assumptions": conversion.get("unverified_geometry", [
                "static zero-head mechanical pose",
                "depth represented with RGB intrinsics; registration unverified",
            ])}
