"""Internal decoded facts backed by temporary disk, for long Episode windows.

The public immutable JSON input stays unchanged. Each spilled observation is
validated before insertion and replayed in arrival order for the same engine.
"""

import math
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory

from .models import ActionObservation, JointObservation


class SpilledObservations:
    def __init__(self, database, kind, model):
        self.database, self.kind, self.model = database, kind, model

    def __len__(self):
        return self.database.execute(
            "SELECT count(*) FROM observations WHERE kind=?", (self.kind,)
        ).fetchone()[0]

    def __iter__(self):
        for (document,) in self.database.execute(
            "SELECT document FROM observations WHERE kind=? ORDER BY sequence", (self.kind,)
        ):
            yield self.model.model_validate_json(document)


class SpilledOffsets:
    def __init__(self, database, topic):
        self.database, self.topic = database, topic

    def __len__(self):
        return self.database.execute(
            "SELECT count(*) FROM offsets WHERE topic=?", (self.topic,)
        ).fetchone()[0]

    def __iter__(self):
        for (value,) in self.database.execute(
            "SELECT value FROM offsets WHERE topic=? ORDER BY sequence", (self.topic,)
        ):
            yield value

    def absolute_percentile(self, quantile):
        count = len(self)
        if not count:
            return None
        return self.database.execute(
            "SELECT abs(value) FROM offsets WHERE topic=? ORDER BY abs(value) LIMIT 1 OFFSET ?",
            (self.topic, max(0, math.ceil(quantile * count) - 1)),
        ).fetchone()[0]


class SpilledQualityInput:
    """Context-owned input view; close only after report and evidence generation."""

    def __init__(self, base):
        if base.actions or base.joints or base.modality_offsets_ns:
            raise ValueError("spilled facts require an empty numeric base")
        self.base = base

    def __getattr__(self, name):
        return getattr(self.base, name)

    def __enter__(self):
        self.directory = TemporaryDirectory(prefix="hc-quality-facts-")
        self.database = sqlite3.connect(str(Path(self.directory.name) / "facts.sqlite3"))
        self.database.executescript(
            "CREATE TABLE observations(sequence INTEGER PRIMARY KEY,kind TEXT,document TEXT);"
            "CREATE INDEX observations_kind ON observations(kind,sequence);"
            "CREATE TABLE offsets(sequence INTEGER PRIMARY KEY,topic TEXT,value INTEGER);"
            "CREATE INDEX offsets_topic ON offsets(topic,sequence);"
            "CREATE INDEX offsets_absolute ON offsets(topic,abs(value));"
        )
        self.actions = SpilledObservations(self.database, "action", ActionObservation)
        self.joints = SpilledObservations(self.database, "joint", JointObservation)
        self.modality_offsets_ns = {}
        return self

    def add(self, kind, observation):
        model = {"action": ActionObservation, "joint": JointObservation}[kind]
        observation = model.model_validate(observation.model_dump())
        if not self.start_ns <= observation.timestamp_ns < self.end_ns:
            raise ValueError("spilled observation is outside its frozen window")
        self.database.execute(
            "INSERT INTO observations(kind,document) VALUES (?,?)",
            (kind, observation.model_dump_json()),
        )

    def add_offset(self, topic, value):
        if not isinstance(topic, str) or not topic.strip() or type(value) is not int:
            raise ValueError("offset requires a nonempty topic and exact integer nanoseconds")
        self.database.execute("INSERT INTO offsets(topic,value) VALUES (?,?)", (topic, value))
        if topic not in self.modality_offsets_ns:
            self.modality_offsets_ns[topic] = SpilledOffsets(self.database, topic)

    def __exit__(self, *_):
        self.database.close()
        self.directory.cleanup()
