import json
from dataclasses import dataclass


@dataclass(frozen=True)
class Sample:
    source_id: str
    source_seq: int
    capture_time_ns: int
    receive_time_ns: int
    arrival_time_ns: int
    clock_epoch: int
    document: bytes

    def data(self):
        return json.loads(self.document)

    @property
    def nbytes(self):
        return len(self.document) + 256
