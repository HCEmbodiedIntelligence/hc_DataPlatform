"""Decoder probe restricted to the deterministic, disposable BE22 MCAP fixture."""

from __future__ import annotations


class Wave2FixtureDecoderProbe:
    def supports(self, message_encoding: str, schema_encoding: str) -> bool:
        return (message_encoding, schema_encoding) == ("cdr", "ros2msg")

    def probe(
        self,
        *,
        message_encoding: str,
        schema_encoding: str,
        schema_name: str = "",
        schema_data: bytes,
        message_data: bytes,
    ) -> None:
        if not self.supports(message_encoding, schema_encoding):
            raise ValueError("the BE22 decoder only supports its cdr/ros2msg fixture")
        if not schema_name or not schema_data:
            raise ValueError("the BE22 fixture requires its checked-in schema")
        topic, separator, sequence = message_data.rpartition(b":")
        if not separator or not topic.startswith(b"/") or not sequence.isdigit():
            raise ValueError("message bytes are not a deterministic BE22 fixture sample")


def factory() -> Wave2FixtureDecoderProbe:
    return Wave2FixtureDecoderProbe()
