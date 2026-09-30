"""Publication JSON v1: UTF-8 (unescaped Unicode), sorted keys, no NaN.

Episode manifests use a distinct ASCII-escaped hc-json/v1 representation.
Do not substitute this serializer for that contract.
"""

import json


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
