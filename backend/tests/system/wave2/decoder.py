"""Decoder probe restricted to the deterministic, disposable BE22 MCAP fixture."""

from __future__ import annotations

import base64
import hashlib
import io
import json

from PIL import Image


class Wave2FixtureDecoderProbe:
    def supports(self, message_encoding: str, schema_encoding: str) -> bool:
        return (message_encoding, schema_encoding) in {
            ("cdr", "ros2msg"),
            ("json", "jsonschema"),
        }

    def probe(
        self,
        *,
        message_encoding: str,
        schema_encoding: str,
        schema_name: str = "",
        schema_data: bytes,
        message_data: bytes,
    ) -> object:
        if not self.supports(message_encoding, schema_encoding):
            raise ValueError("the BE22 decoder does not support this fixture encoding")
        if not schema_name or not schema_data:
            raise ValueError("the BE22 fixture requires its checked-in schema")
        if (message_encoding, schema_encoding) == ("json", "jsonschema"):
            payload = json.loads(message_data)
            encoded = base64.b64decode(payload["data_base64"], validate=True)
            if payload.get("encoding") != "jpeg":
                raise ValueError("camera fixture is not JPEG")
            if hashlib.sha256(encoded).hexdigest() != payload.get("jpeg_sha256"):
                raise ValueError("camera fixture hash is invalid")
            with Image.open(io.BytesIO(encoded)) as image:
                image.load()
                if (
                    image.format != "JPEG"
                    or image.width != payload.get("width")
                    or image.height != payload.get("height")
                ):
                    raise ValueError("camera fixture dimensions are invalid")
            return payload
        topic, separator, sequence = message_data.rpartition(b":")
        if not separator or not topic.startswith(b"/") or not sequence.isdigit():
            raise ValueError("message bytes are not a deterministic BE22 fixture sample")
        index = int(sequence)
        if topic == b"/joint_states":
            return [index / 100.0, index / 200.0, -index / 300.0]
        if topic == b"/action":
            return [index / 50.0, 1.0 if index % 2 else 0.0]
        return {"label": topic.decode("utf-8"), "sequence": index}


def factory() -> Wave2FixtureDecoderProbe:
    return Wave2FixtureDecoderProbe()
