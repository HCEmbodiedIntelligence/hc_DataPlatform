"""Read actual archives and MCAP messages, not just successful job statuses."""

import hashlib
import json
from types import SimpleNamespace

import pytest
from mcap.reader import make_reader
from mcap.writer import Writer

from hc_data_platform.episode_store.collection_delivery_models import RequestCollectionDelivery
from hc_data_platform.episode_store.mcap_export import slice_original
from hc_data_platform.episode_store.original_export import write_original_collection
from tests.episode_store.test_collection_delivery_services import queued  # noqa
from tests.episode_store.test_collection_export_services import pair  # noqa
from tests.episode_store.test_commit_services import scoped
from tests.episode_store.test_complete_delivery_services import run_job


@pytest.mark.integration
def test_lerobot_to_mcap_is_rejected_before_queueing(queued):  # noqa: F811
    from hc_data_platform.core.errors import ProblemException

    service, source, body, auth = queued
    body = RequestCollectionDelivery.model_validate({**body.model_dump(), "output_format": "mcap"})
    with scoped(source), pytest.raises(ProblemException) as caught:
        service.request_collection(body, "mcap-export", auth)
    assert caught.value.problem.status == 422
    assert caught.value.problem.code == "EPISODE_DELIVERY_MCAP_ORIGINAL_REQUIRED"


def test_slice_keeps_ros_field_keys_schema_channel_metadata_and_exact_bytes(tmp_path):
    source, target = tmp_path / "raw.mcap", tmp_path / "slice.mcap"
    with source.open("wb") as stream:
        writer = Writer(stream, enable_crcs=True, enable_data_crcs=True)
        writer.start(profile="ros2")
        schema = writer.register_schema(
            "custom/Joint", "ros2msg", b"float64[] position\nstring[] name\n"
        )
        channel = writer.register_channel(
            "/robot/joints", "cdr", schema, metadata={"calibration": "one"}
        )
        for i in range(5):
            writer.add_message(channel, 100 + i * 10, bytes([0, 1, 2, i]), 99 + i * 10, i)
        writer.add_attachment(0, 0, "calibration.json", "application/json", b'{"unit":"rad"}')
        writer.add_metadata("device", {"robot": "arm"})
        writer.finish()
    before = source.read_bytes()
    assert (
        slice_original(
            source, target, start_ns=110, end_ns=140, annotation={"tags": [{"key": "pick"}]}
        )
        == 3
    )

    def messages(path):
        with path.open("rb") as stream:
            return [
                (
                    s.name,
                    s.encoding,
                    s.data,
                    c.topic,
                    c.message_encoding,
                    c.metadata,
                    m.log_time,
                    m.publish_time,
                    m.sequence,
                    m.data,
                )
                for s, c, m in make_reader(stream, validate_crcs=True).iter_messages()
            ]

    assert messages(target) == messages(source)[1:4]
    assert source.read_bytes() == before
    with target.open("rb") as stream:
        r = make_reader(stream, validate_crcs=True)
        attachments = {a.name: a.data for a in r.iter_attachments()}
        assert attachments["calibration.json"] == b'{"unit":"rad"}'
        assert json.loads(attachments["hc/annotation.json"])["tags"][0]["key"] == "pick"
        assert next(r.iter_metadata()).metadata == {"robot": "arm"}


def test_originals_keep_bytes_with_annotation_sidecars_and_deduplicate(tmp_path):
    raw = b"original video bytes\x00\x01"
    ref = {
        "relative_path": "video.mp4",
        "object": {
            "key": "scoped/raw/video",
            "size": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        },
    }
    package = SimpleNamespace(
        document=lambda _: {"original_inventory": [ref], "episode_index": 3},
        receipt=SimpleNamespace(data=SimpleNamespace(model_dump=lambda **_: {"episode": "three"})),
        manifest=SimpleNamespace(files=[]),
    )
    selected = SimpleNamespace(
        package=package,
        annotation_file=SimpleNamespace(content=b'{"tags":[{"label":"pick"}]}'),
        review_document=None,
    )
    storage = SimpleNamespace(read_chunks=lambda _: iter([raw[:3], raw[3:]]))
    manifest, _ = write_original_collection(tmp_path / "out", [selected, selected], storage=storage)
    files = list((tmp_path / "out/sources").rglob("*.mp4"))
    assert len(files) == 1 and files[0].read_bytes() == raw
    assert len(manifest["members"]) == 2
    assert (
        json.loads((tmp_path / "out/selections/000000.json").read_bytes())["annotations"]["tags"][
            0
        ]["label"]
        == "pick"
    )
    bad = SimpleNamespace(read_chunks=lambda _: iter([b"corrupted"]))
    with pytest.raises(ValueError, match="hash"):
        write_original_collection(tmp_path / "bad", [selected], storage=bad)


@pytest.mark.integration
def test_registered_adapter_uses_existing_queue_without_dispatch_changes(
    queued,  # noqa: F811
    tmp_path,
    monkeypatch,
):
    from hc_data_platform.episode_store import export_formats
    from hc_data_platform.episode_store.original_export import inventory

    def writer(root, selections, **kwargs):
        root.mkdir()
        (root / "fixture.json").write_text(
            json.dumps({"episodes": [s.package.receipt.data.data_version_id for s in selections]})
        )
        return inventory(root, [], "synthetic-test/1")

    monkeypatch.setattr(export_formats, "_FORMATS", dict(export_formats._FORMATS))
    export_formats.register(
        export_formats.ExportFormat(
            "synthetic",
            "synthetic_collection_zip/1",
            "Test adapter",
            False,
            writer,
            reader_version="synthetic-test/1",
        )
    )
    service, source, body, auth = queued
    body = RequestCollectionDelivery.model_validate(
        {**body.model_dump(), "output_format": "synthetic"}
    )
    with scoped(source):
        job = service.request_collection(body, "custom-adapter", auth)
        run_job(service, job)
        ready = service.status(job.job_id, auth)
        assert ready.status == "READY"
        assert ready.plan.reader_version == "synthetic-test/1"
        assert any(f["id"] == "synthetic" for f in export_formats.formats())


@pytest.mark.parametrize("kind", ["automatic", "bare", "transport", "lerobot"])
def test_original_mcap_eligibility_and_automatic_ros_preference(kind):
    from hc_data_platform.core.errors import ProblemException
    from hc_data_platform.episode_store.original_export import require_original_mcap

    def entry(name):
        return {
            "relative_path": name,
            "object": {"key": "source/" + name, "size": 10, "sha256": "a" * 64},
        }

    source = {
        "start_offset_ns": 10,
        "end_offset_ns": 20,
        "clock": {"recording_origin_ns": 100},
        "inventory": [entry("source/original/source.mcap")],
    }
    if kind == "automatic":
        source["inventory"] = [
            entry("source/original/" + name) for name in ("raw.mcap", "originals.mcap")
        ]
    elif kind == "transport":
        source["mcap"] = {"source_profile": "hc-episode-mcap/1"}
    elif kind == "lerobot":
        source = {"raw_upload_id": "lerobot-upload"}
    package = SimpleNamespace(document=lambda _: source)
    if kind in {"transport", "lerobot"}:
        with pytest.raises(ProblemException, match="MCAP_ORIGINAL_REQUIRED"):
            require_original_mcap(package)
    else:
        file, start, end = require_original_mcap(package)
        assert (start, end) == (110, 120)
        assert file.relative_path.endswith(
            "originals.mcap" if kind == "automatic" else "source.mcap"
        )


@pytest.mark.parametrize("ros", [True, False])
def test_export_rejects_lerobot_transport(tmp_path, ros):
    from hc_data_platform.core.errors import ProblemException
    from hc_data_platform.episode_store.original_export import validate_original_mcap

    path = tmp_path / "input.mcap"
    with path.open("wb") as stream:
        writer = Writer(stream)
        writer.start(profile="ros2" if ros else "hc-episode-mcap/1")
        schema = writer.register_schema(
            "custom/msg/Joint",
            "ros2msg" if ros else "jsonschema",
            b"float64 position" if ros else b"{}",
        )
        channel = writer.register_channel("/joints", "cdr" if ros else "json", schema)
        writer.add_message(channel, 100, b"test", 100)
        writer.finish()
    if ros:
        validate_original_mcap(path)
    else:
        with pytest.raises(ProblemException, match="MCAP_ORIGINAL_REQUIRED"):
            validate_original_mcap(path)


def test_custom_capture_mcap_is_exportable(tmp_path):
    from hc_data_platform.episode_store.original_export import validate_original_mcap

    path = tmp_path / "raw.mcap"
    with path.open("wb") as stream:
        writer = Writer(stream)
        writer.start(profile="humanoid-session/1.3.0")
        schema = writer.register_schema("humanoid-session/1.3.0", "jsonschema", b"{}")
        channel = writer.register_channel("/humanoid/session/raw/arm", "json", schema)
        writer.add_message(channel, 100, b'{"position":[1.2]}', 100)
        writer.finish()
    validate_original_mcap(path)
    slice_original(path, tmp_path / "cut.mcap", start_ns=99, end_ns=101)
    with (tmp_path / "cut.mcap").open("rb") as stream:
        schema, channel, message = next(make_reader(stream).iter_messages())
        assert schema.encoding == "jsonschema" and channel.message_encoding == "json"
        assert message.data == b'{"position":[1.2]}'
