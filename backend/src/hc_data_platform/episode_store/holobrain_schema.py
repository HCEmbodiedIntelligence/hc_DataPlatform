"""RoboOrchard 0.1.0 storage contract, checked against official feature types."""

import hashlib
import json
from pathlib import Path

import numpy as np
import pyarrow as pa

PROFILE = "hc_tj_19dof_robo_orchard_0.1.0/v1"
CONTRACT = json.loads(Path(__file__).with_name("holobrain_contract.json").read_text())
FEATURES = CONTRACT["features"]


def schema():
    tensor = pa.struct([("data", pa.list_(pa.float32())), ("shape", pa.list_(pa.int32()))])
    times = pa.list_(pa.int64())
    joint = pa.struct([(f, tensor) for f in ("position", "velocity", "effort")] +
                      [("names", pa.list_(pa.string())), ("timestamps", times)])
    pose = pa.struct([("xyz", tensor), ("quat", tensor), ("timestamps", times),
                      ("parent_frame_id", pa.string()), ("child_frame_id", pa.string())])
    camera = pa.struct([
        ("topic", pa.string()), ("frame_id", pa.string()), ("image_shape", pa.list_(pa.int32())),
        ("intrinsic_matrices", tensor), ("transform_matrices", tensor),
        ("distortion", pa.struct([("model", pa.string()), ("coefficients", tensor)])),
        ("pose", pose), ("sensor_data", pa.list_(pa.binary())), ("format", pa.string()),
        ("timestamps", times),
    ])
    return pa.schema([("joints", joint), ("actions", joint), ("head", camera), ("head_depth", camera)] +
                     [(key, pa.int64()) for key in list(FEATURES)[4:]],
                     metadata={b"huggingface": json.dumps({"info": {"features": FEATURES}}).encode()})


def tensor(values, shape):
    a = np.asarray(values, dtype=np.float32)
    if a.size != int(np.prod(shape)) or not np.isfinite(a).all():
        raise ValueError("HOLOBRAIN_TENSOR_INVALID")
    return {"data": a.reshape(-1).tolist(), "shape": shape}


def create_database(path, robot, tasks, episodes):
    import duckdb

    db = duckdb.connect(str(path), config={"storage_compatibility_version": "v1.0.0"})
    try:
        tables = {row[0]: row for row in CONTRACT["database"]["table_info"]}
        for name in ("robot", "task", "instruction", "episode", "table_info"):
            # SQLAlchemy's unquoted portable DDL needs the reserved DuckDB identifier quoted.
            import re
            db.execute(re.sub(r"\bindex\b", '"index"', tables[name][3]))
        for _, statement in CONTRACT["database"]["indexes"]:
            db.execute(statement)
        for row in tables.values():
            db.execute("INSERT INTO table_info VALUES (?,?,?,?)", row)
        robot_content = {"name": robot, "content": None, "content_format": None}
        robot_hash = hashlib.md5(json.dumps(robot_content, sort_keys=True).encode()).digest()
        db.execute("INSERT INTO robot VALUES (0,?,NULL,NULL,?)", [robot, robot_hash])
        for index, task in enumerate(tasks):
            # Official Task 0.0.2 canonical content includes normalized info.
            task_content = {"name": task, "description": task, "info": None}
            md5 = hashlib.md5(json.dumps(task_content, sort_keys=True).encode()).digest()
            db.execute("INSERT INTO task VALUES (?,?,?,NULL,?)", [index, task, task, md5])
            content = {"description": task}
            md5 = hashlib.md5((task + json.dumps(content, sort_keys=True)).encode()).digest()
            db.execute("INSERT INTO instruction VALUES (?,?,?,?)", [index, task, json.dumps(content), md5])
        for episode in episodes:
            db.execute("INSERT INTO episode VALUES (?,0,?,NULL,?,?,?,NULL,?)", [
                episode["index"], episode["task_index"], episode["begin"], episode["frames"],
                episode["truncated"],
                json.dumps(episode["info"], ensure_ascii=False),
            ])
        db.execute("CHECKPOINT")
    finally:
        db.close()
