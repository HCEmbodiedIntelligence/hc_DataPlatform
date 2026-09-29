"""Disk-bounded, time-ordered projection of decoded MCAP messages.

Video codecs can delay or reorder frames. Spooling before exposing the stream
keeps other modalities from advancing the alignment watermark past those frames.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterable, Iterator
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .mcap_camera import CameraFrame, McapCameraDecoder


@dataclass(frozen=True)
class ProjectionRow:
    topic: str
    timestamp_ns: int
    is_camera: bool
    value_json: bytes | None
    image: bytes | None
    luma_mean: float | None
    fingerprint: str | None
    perceptual_hash: str | None
    corrupt: bool


def build_projection(
    path: Path,
    messages: Iterable[tuple[Any, Any, Any]],
    *,
    camera_topics: set[str],
    expected_topics: set[str],
    decoder: Any,
    normalize_value: Callable[[object], object],
) -> None:
    cameras = McapCameraDecoder(decoder)
    counts: dict[str, int] = {}
    with closing(sqlite3.connect(path)) as database, database:
        database.execute("PRAGMA cache_size=-8192")
        database.execute("PRAGMA temp_store=FILE")
        database.execute("""CREATE TABLE samples (
            ordinal INTEGER PRIMARY KEY, topic TEXT NOT NULL, stamp INTEGER NOT NULL,
            camera INTEGER NOT NULL, value BLOB, image BLOB, luma REAL,
            fingerprint TEXT, perceptual_hash TEXT, corrupt INTEGER NOT NULL)""")

        def image(frame: CameraFrame) -> None:
            observation = frame.observation
            database.execute(
                "INSERT INTO samples VALUES(NULL,?,?,?,?,?,?,?,?,?)",
                (
                    frame.topic,
                    observation.timestamp_ns,
                    1,
                    None,
                    frame.image,
                    observation.luma_mean,
                    observation.fingerprint,
                    frame.perceptual_hash,
                    int(observation.corrupt),
                ),
            )

        for schema, channel, message in messages:
            topic = str(channel.topic)
            counts[topic] = counts.get(topic, 0) + 1
            if topic in camera_topics:
                for frame in cameras.decode(schema, channel, message):
                    image(frame)
            else:
                if (
                    schema is None
                    or decoder is None
                    or not decoder.supports(channel.message_encoding, schema.encoding)
                ):
                    raise ValueError(f"topic {topic!r} has no supported value decoder")
                value = normalize_value(
                    decoder.probe(
                        message_encoding=channel.message_encoding,
                        schema_encoding=schema.encoding,
                        schema_name=schema.name,
                        schema_data=schema.data,
                        message_data=message.data,
                    )
                )
                database.execute(
                    "INSERT INTO samples VALUES(NULL,?,?,?,?,?,?,?,?,?)",
                    (
                        topic,
                        int(message.log_time),
                        0,
                        json.dumps(
                            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                        ).encode(),
                        None,
                        None,
                        None,
                        None,
                        0,
                    ),
                )
        for frame in cameras.finish():
            image(frame)
        if set(counts) != expected_topics:
            raise ValueError("the MCAP topic inventory differs from the committed Manifest")
        database.execute("CREATE INDEX samples_time ON samples(stamp,ordinal)")


def read_projection(path: Path, *, include_images: bool = True) -> Iterator[ProjectionRow]:
    with closing(sqlite3.connect(path)) as database:
        image_column = "image" if include_images else "NULL"
        for row in database.execute(
            f"SELECT topic,stamp,camera,value,{image_column},luma,fingerprint,"
            "perceptual_hash,corrupt "
            "FROM samples ORDER BY stamp,ordinal"
        ):
            # SQLite stores booleans as integers; Arrow's bool columns require
            # actual bool values when this projection is materialized for recovery.
            yield ProjectionRow(*row[:2], bool(row[2]), *row[3:8], bool(row[8]))
