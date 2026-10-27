"""Streaming numeric statistics shared by package writing and capture selection."""

import numpy as np


class Moments:
    def __init__(self):
        self.count = 0
        self.mean = self.m2 = self.minimum = self.maximum = None

    def add(self, values):
        array = np.asarray(values, dtype=np.float64)
        if array.ndim == 1:
            array = array[:, None]
        if not np.isfinite(array).all():
            raise ValueError("numeric feature contains nonfinite values")
        count, mean = len(array), array.mean(axis=0)
        m2 = np.square(array - mean).sum(axis=0)
        minimum, maximum = array.min(axis=0), array.max(axis=0)
        if not self.count:
            self.count, self.mean, self.m2 = count, mean, m2
            self.minimum, self.maximum = minimum, maximum
            return
        delta, total = mean - self.mean, self.count + count
        self.m2 += m2 + np.square(delta) * self.count * count / total
        self.mean += delta * count / total
        self.minimum = np.minimum(self.minimum, minimum)
        self.maximum = np.maximum(self.maximum, maximum)
        self.count = total

    def document(self):
        return {
            "count": [self.count],
            "min": self.minimum.tolist(),
            "max": self.maximum.tolist(),
            "mean": self.mean.tolist(),
            "std": np.sqrt(self.m2 / self.count).tolist(),
        }


def refresh_numeric_statistics(root):
    """Recompute dense indices and valid numerical features after selection."""
    import json

    import pyarrow as pa
    import pyarrow.parquet as pq

    from hc_data_platform.data_formats.lerobot_layout import DATA_PATH, EPISODES_PATH

    from .models import canonical

    info = json.loads((root / "meta/info.json").read_bytes())
    stats = json.loads((root / "meta/stats.json").read_bytes())
    moments = {
        k: Moments()
        for k, f in info["features"].items()
        if f["dtype"] not in {"string", "binary", "video"}
    }
    for batch in pq.ParquetFile(root / DATA_PATH).iter_batches():
        columns = batch.to_pydict()
        for key, accumulator in moments.items():
            mask = columns.get("hc.valid." + key, [True] * batch.num_rows)
            values = [v for v, valid in zip(columns[key], mask, strict=True) if valid]
            if values:
                accumulator.add(values)
    for key, accumulator in moments.items():
        stats.pop(key, None)
        if accumulator.count:
            stats[key] = accumulator.document()
    episode = pq.read_table(root / EPISODES_PATH).to_pylist()[0]
    episode = {k: v for k, v in episode.items() if not k.startswith("stats/")}
    for key, values in stats.items():
        for name, value in values.items():
            episode[f"stats/{key}/{name}"] = value
    pq.write_table(pa.Table.from_pylist([episode]), root / EPISODES_PATH)
    (root / "meta/stats.json").write_bytes(canonical(stats))
