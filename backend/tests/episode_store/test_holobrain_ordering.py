import pytest
from mcap.reader import make_reader
from mcap.writer import Writer

from hc_data_platform.episode_store.holobrain_export import RGB, synchronized
from tests.episode_store.test_holobrain_export import source


def rewrite_images(source_path, target, *, duplicate=False):
    with source_path.open("rb") as incoming, target.open("wb") as outgoing:
        writer = Writer(outgoing)
        writer.start(profile="ros2")
        schemas, channels = {}, {}
        for schema, channel, message in make_reader(incoming).iter_messages():
            if schema.id not in schemas:
                schemas[schema.id] = writer.register_schema(
                    schema.name, schema.encoding, schema.data
                )
            if channel.id not in channels:
                channels[channel.id] = writer.register_channel(
                    channel.topic, channel.message_encoding, schemas[schema.id], channel.metadata
                )
            log_time = message.log_time
            if channel.topic == RGB and message.sequence == 1 and not duplicate:
                log_time += 50_000_000
            writer.add_message(
                channels[channel.id], log_time, message.data, message.publish_time, message.sequence
            )
            if duplicate and channel.topic == RGB and message.sequence == 1:
                writer.add_message(
                    channels[channel.id],
                    log_time + 1,
                    message.data,
                    message.publish_time,
                    message.sequence,
                )
        writer.finish()


def collect(path, folder):
    folder.mkdir()
    return [
        row
        for row, _ in synchronized(
            path, folder, {"candidate_frames": 0, "sync_drops": []}, lambda: None
        )
    ]


def test_delayed_rgb_is_ordered_without_changing_any_exported_value(tmp_path):
    original = source(tmp_path)["path"]
    delayed = tmp_path / "delayed.mcap"
    rewrite_images(original, delayed)
    expected = collect(original, tmp_path / "before")
    actual = collect(delayed, tmp_path / "after")
    assert len(actual) == 5
    assert actual == expected


def test_duplicate_acquisition_time_remains_an_error(tmp_path):
    original = source(tmp_path)["path"]
    duplicated = tmp_path / "duplicate.mcap"
    rewrite_images(original, duplicated, duplicate=True)
    with pytest.raises(ValueError, match="HOLOBRAIN_DUPLICATE_ACQUISITION_TIME"):
        collect(duplicated, tmp_path / "rejected")
