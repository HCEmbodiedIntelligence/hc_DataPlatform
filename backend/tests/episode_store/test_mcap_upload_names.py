import hashlib
from types import SimpleNamespace

import pytest

from hc_data_platform.episode_store.export_formats import from_archive, get_format
from hc_data_platform.episode_store.mcap_upload_names import name_members, upload_stem


def receipt(path, data):
    sha = hashlib.sha256(data).hexdigest()
    return {
        "source_object": dict(path=path, object_key="original", size=len(data), sha256=sha),
        "selected_mcap": {"source_sha256": sha},
    }


def test_original_member_bytes_and_name_are_independent_of_output(tmp_path):
    blocks = [b"original", b" bytes"]
    reads = []

    def chunks(key):
        reads.append(key)
        return iter(blocks)

    storage = SimpleNamespace(read_chunks=chunks)
    origin = receipt("inside.zip/camera.v2.MCAP", b"original bytes")
    cache = {}
    name = upload_stem(origin, storage, lambda: None, cache)
    assert name == "camera.v2_" + hashlib.md5(b"original bytes", usedforsecurity=False).hexdigest()
    origin["source_object"]["path"] = "other/upload.mcap"
    alias = upload_stem(origin, storage, lambda: None, cache)
    assert reads == ["original"]
    members = []
    stems = {"first": name, "second": alias}
    for index, source in enumerate(("first", "first", "second")):
        old = f"episode-{index:06d}"
        for ext in (".mcap", "-frames.json", "-normalization.json"):
            (tmp_path / (old + ext)).write_text(str(index))
        members.append({"path": old + ".mcap", "data": {"data_version_id": source}})
    name_members(tmp_path, members, stems)
    expected = [f"episode-000000/{name}", f"episode-000001/{name}", alias]
    for index, stem in enumerate(expected):
        assert members[index]["path"] == stem + ".mcap"
        for suffix in (".mcap", "-frames.json", "-normalization.json"):
            assert (tmp_path / (stem + suffix)).read_text() == str(index)


def test_corrupt_original_cannot_supply_a_digest():
    origin = receipt("file.mcap", b"abc")
    with pytest.raises(ValueError, match="frozen receipt"):
        upload_stem(origin, SimpleNamespace(read_chunks=lambda key: [b"xyz"]), lambda: None, {})


def test_named_exports_have_separate_cache_identity():
    current = get_format("mcap")
    assert current.archive == "mcap_collection_zip/9" and current.writer.keywords["fast_png"]
    assert "fast_png" not in from_archive("mcap_collection_zip/8").writer.keywords
    assert from_archive("mcap_collection_zip/6").writer.keywords["upload_names"]
    assert "upload_names" not in from_archive("mcap_collection_zip/5").writer.keywords


def test_original_contract_uses_frozen_channel_aliases(tmp_path):
    from mcap.writer import Writer
    from hc_data_platform.episode_store.committed_mcap import IMAGE_TOPICS, PARAMETERS, source_contract

    topics = ("/observation/joint_states", *IMAGE_TOPICS.values(), *PARAMETERS)
    aliases = {f"/upload/{i}": topic for i, topic in enumerate(topics)}
    path = tmp_path / "upload.mcap"
    with path.open("wb") as stream:
        writer = Writer(stream)
        writer.start()
        for raw, topic in aliases.items():
            kind = ("std_msgs/String" if topic in PARAMETERS else
                    "sensor_msgs/CompressedImage" if topic in IMAGE_TOPICS.values() else "sensor_msgs/JointState")
            schema = writer.register_schema(kind, "ros2msg", b"string data")
            channel = writer.register_channel(raw, "cdr", schema)
            if topic in PARAMETERS:
                writer.add_message(channel, 123, raw.encode(), 123)
        writer.finish()

    schemas, parameters = source_contract(path, aliases)
    assert schemas["/action/joint_states"].name == "sensor_msgs/msg/JointState"
    assert parameters == {topic: [(123, raw.encode())] for raw, topic in aliases.items() if topic in PARAMETERS}
    ambiguous = {**aliases, "/upload/1": topics[0]}
    assert source_contract(path, ambiguous) is None
