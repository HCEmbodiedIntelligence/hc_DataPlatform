from __future__ import annotations

from typing import Annotated, Any

from pydantic import BeforeValidator, PlainSerializer

INT64_MIN = -(2**63)
INT64_MAX = 2**63 - 1


def _parse_int64(value: Any) -> int:
    if isinstance(value, bool) or isinstance(value, float):
        raise ValueError("int64 must be supplied as a decimal string or integer")
    if isinstance(value, str):
        if (
            not value
            or (value[0] == "-" and not value[1:].isdigit())
            or (value[0] != "-" and not value.isdigit())
        ):
            raise ValueError("invalid decimal int64")
        if len(value) > 1 and value[0] == "0":
            raise ValueError("decimal int64 must be canonical")
        if value.startswith("-0"):
            raise ValueError("decimal int64 must be canonical")
    parsed = int(value)
    if not INT64_MIN <= parsed <= INT64_MAX:
        raise ValueError("int64 out of range")
    return parsed


def _parse_non_negative(value: Any) -> int:
    parsed = _parse_int64(value)
    if parsed < 0:
        raise ValueError("value must be non-negative")
    return parsed


Int64 = Annotated[
    int,
    BeforeValidator(_parse_int64),
    PlainSerializer(lambda value: str(value), return_type=str, when_used="always"),
]
ByteCount = Annotated[
    int,
    BeforeValidator(_parse_non_negative),
    PlainSerializer(lambda value: str(value), return_type=str, when_used="always"),
]
Nanoseconds = Annotated[
    int,
    BeforeValidator(_parse_non_negative),
    PlainSerializer(lambda value: str(value), return_type=str, when_used="always"),
]

int64 = Int64
bytes_decimal = ByteCount
nanoseconds = Nanoseconds
Int64Str = Int64
Bytes = ByteCount
