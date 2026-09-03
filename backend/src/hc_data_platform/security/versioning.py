from __future__ import annotations

import re
from dataclasses import dataclass

from hc_data_platform.core.errors import problem

_ETAG_PATTERN = re.compile(r'^"v([1-9][0-9]*)"$')


def etag_mismatch(expected: str) -> Exception:
    return problem(
        status=412,
        code="ETAG_MISMATCH",
        title="Resource version mismatch",
        detail="The resource changed after it was read.",
        details={"expected": expected},
    )


@dataclass(frozen=True, slots=True)
class ResourceVersion:
    value: int = 1

    def __post_init__(self) -> None:
        if isinstance(self.value, bool) or self.value < 1:
            raise ValueError("resource version must be a positive integer")

    @property
    def etag(self) -> str:
        return f'"v{self.value}"'

    @classmethod
    def from_etag(cls, value: str) -> ResourceVersion:
        matched = _ETAG_PATTERN.fullmatch(value)
        if matched is None:
            raise etag_mismatch('"v<current>"')
        return cls(int(matched.group(1)))

    def require(self, if_match: str) -> None:
        if if_match != self.etag:
            raise etag_mismatch(self.etag)

    def next(self, if_match: str) -> ResourceVersion:
        self.require(if_match)
        return ResourceVersion(self.value + 1)
