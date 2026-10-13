"""Preserve original MCAP messages; read legacy package transports for compatibility."""

import hashlib
import json
from pathlib import Path, PurePosixPath

from mcap.reader import make_reader
from mcap.writer import Writer

from .models import canonical

PROFILE = "hc-episode-mcap/1"
MAX_ATTACHMENT = 8 * 1024**2
PART_PREFIX = ".hc-mcap-parts/"


def extract_episode(path, destination):
    """Reconstruct only verified, safe package members from our versioned transport."""
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    with Path(path).open("rb") as stream:
        reader = make_reader(stream, validate_crcs=True)
        if reader.get_header().profile != PROFILE:
            raise ValueError("not a platform Episode MCAP")
        contract = None
        found = {}
        for attachment in reader.iter_attachments():
            name = attachment.name
            pure = PurePosixPath(name)
            if (
                not name
                or str(pure) != name
                or pure == PurePosixPath(".")
                or pure.is_absolute()
                or ".." in pure.parts
                or "\\" in name
                or name in found
            ):
                raise ValueError("invalid or duplicate MCAP attachment path")
            if len(attachment.data) > MAX_ATTACHMENT:
                raise ValueError("MCAP attachment exceeds limit")
            if name == "hc/mcap-contract.json":
                if contract is not None:
                    raise ValueError("duplicate MCAP contract")
                contract = json.loads(attachment.data)
                continue
            target = destination / pure
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(attachment.data)
            found[name] = (len(attachment.data), hashlib.sha256(attachment.data).hexdigest())
        if not contract or contract.get("format") != PROFILE:
            raise ValueError("missing MCAP transport contract")
        expected = {e["path"]: (e["size"], e["sha256"]) for e in contract["files"]}
        for name, names in contract.get("parts", {}).items():
            pure = PurePosixPath(name)
            if (
                name not in expected
                or not name
                or str(pure) != name
                or pure.is_absolute()
                or ".." in pure.parts
                or "\\" in name
                or name.startswith(PART_PREFIX)
                or name in found
            ):
                raise ValueError("invalid split MCAP file")
            if (
                not names
                or len(set(names)) != len(names)
                or any(n not in found or not n.startswith(PART_PREFIX) for n in names)
            ):
                raise ValueError("invalid split MCAP parts")
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            sha, size = hashlib.sha256(), 0
            with target.open("xb") as output:
                for part in names:
                    with (destination / part).open("rb") as incoming:
                        for body in iter(lambda: incoming.read(1024**2), b""):
                            size += len(body)
                            if size > expected[name][0]:
                                raise ValueError("split MCAP file exceeds inventory size")
                            sha.update(body)
                            output.write(body)
                    (destination / part).unlink()
                    del found[part]
            found[name] = (size, sha.hexdigest())
        if found != expected or len(expected) != len(contract["files"]):
            raise ValueError("MCAP attachment inventory/hash mismatch")
        return contract


def slice_original(source, output, *, start_ns, end_ns, annotation=None, checkpoint=lambda: None):
    """Copy original messages byte-for-byte in the half-open log-time interval."""
    if not 0 <= start_ns < end_ns:
        raise ValueError("invalid MCAP source window")
    count = 0
    with Path(source).open("rb") as incoming, Path(output).open("wb") as outgoing:
        reader = make_reader(incoming, validate_crcs=True)
        writer = Writer(outgoing, enable_crcs=True, enable_data_crcs=True)
        writer.start(profile=reader.get_header().profile, library="hc-data-platform")
        schemas, channels = {}, {}
        for schema, channel, message in reader.iter_messages(start_time=start_ns, end_time=end_ns):
            checkpoint()
            if schema and schema.id not in schemas:
                schemas[schema.id] = writer.register_schema(
                    schema.name, schema.encoding, schema.data
                )
            if channel.id not in channels:
                channels[channel.id] = writer.register_channel(
                    channel.topic,
                    channel.message_encoding,
                    schemas.get(channel.schema_id, 0),
                    metadata=channel.metadata,
                )
            writer.add_message(
                channels[channel.id],
                message.log_time,
                message.data,
                message.publish_time,
                message.sequence,
            )
            count += 1
        for attachment in reader.iter_attachments():
            checkpoint()
            if attachment.name == "hc/annotation.json":
                continue
            writer.add_attachment(
                attachment.create_time,
                attachment.log_time,
                attachment.name,
                attachment.media_type,
                attachment.data,
            )
        for metadata in reader.iter_metadata():
            writer.add_metadata(metadata.name, metadata.metadata)
        if annotation is not None:
            writer.add_attachment(
                start_ns, start_ns, "hc/annotation.json", "application/json", canonical(annotation)
            )
        writer.finish()
    if not count:
        raise ValueError("MCAP slice contains no source messages")
    return count
