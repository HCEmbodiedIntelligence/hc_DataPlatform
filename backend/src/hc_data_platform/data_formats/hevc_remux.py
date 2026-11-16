"""Copy verified HEVC pictures into a new CFR file without re-encoding.

Only safe when every selected picture decodes independently. Each selected
packet must be a keyframe; otherwise ``NotIntraCoded`` is raised before any
output is accepted, so callers can fall back to a full encode. Callers must
still decode the output and compare its codes against the expected hash.
"""
from fractions import Fraction
from pathlib import Path


class NotIntraCoded(ValueError):
    pass


def remux_hevc_frames(source: Path, destination: Path, indices, *, fps) -> int:
    """Write source frames ``indices`` (nondecreasing, repeats allowed) at ``fps``."""
    import av

    indices = tuple(indices)
    if not indices or indices[0] < 0 or any(b < a for a, b in zip(indices, indices[1:], strict=False)):
        raise ValueError("remux requires nondecreasing frame indices")
    rate = Fraction(fps)
    if rate <= 0:
        raise ValueError("remux frame rate must be positive")
    time_base = 1 / rate
    copied = 0
    with av.open(str(source)) as incoming:
        original = incoming.streams.video[0]
        if original.codec_context.name != "hevc" or original.codec_context.has_b_frames:
            raise NotIntraCoded("remux source is not an HEVC stream without reordering")
        with av.open(str(destination), "w") as outgoing:
            output = outgoing.add_stream_from_template(original)
            output.time_base = time_base
            frames = (packet for packet in incoming.demux(original) if packet.size)
            for index, packet in enumerate(frames):
                if index < indices[copied]:
                    continue
                if not packet.is_keyframe:
                    raise NotIntraCoded("remux source frame is not independently decodable")
                payload = bytes(packet)
                while copied < len(indices) and indices[copied] == index:
                    clone = av.Packet(payload)
                    clone.stream = output
                    clone.pts = clone.dts = copied
                    clone.duration = 1
                    clone.time_base = time_base
                    clone.is_keyframe = True
                    outgoing.mux(clone)
                    copied += 1
                if copied == len(indices):
                    break
    if copied != len(indices):
        raise ValueError("remux source ended before every requested frame")
    return copied
