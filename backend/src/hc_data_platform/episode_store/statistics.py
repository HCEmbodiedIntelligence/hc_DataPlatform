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
