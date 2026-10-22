"""Historical ingress labels may differ around identical committed numeric output."""

import copy
import hashlib
import io
import json
import zipfile
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import numpy as np
import pyarrow.parquet as pq
import pytest

from hc_data_platform.capture_schemes.models import OutputContract
from hc_data_platform.episode_store.collection_delivery_models import (
    CollectionDeliveryPlan,
    RequestCollectionDelivery,
)
from hc_data_platform.episode_store.collection_export import (
    PROCESSOR,
    CollectionIncompatible,
    CollectionSource,
    write_collection,
)
from hc_data_platform.episode_store.collection_metadata import NORMALIZED_PROCESSOR
from hc_data_platform.episode_store.errors import PackageConflict
from hc_data_platform.episode_store.models import CommitReceipt, DataVersionRef, PackageInput
from hc_data_platform.episode_store.prepared_assets import PreparedAssets
from hc_data_platform.episode_store.service import EpisodePackageService
from hc_data_platform.episode_store.writer import write_episode
from hc_data_platform.lance_catalog.models import StepRecord
from tests.episode_store.test_commit_services import scoped
from tests.episode_store.test_complete_delivery_services import run_job
from tests.episode_store.test_detail_services import auth_for
from tests.episode_store.test_writer import identity


@pytest.fixture
def source_factory(tmp_path):
    organization = "collection-metadata-" + uuid4().hex
    count = 0

    def build(
        *,
        robot_type="unknown",
        value_key="values",
        scalar=False,
        robot_id="robot-1",
        schema_id="capture-fixture",
        capture=True,
    ):
        nonlocal count
        count += 1
        ident = identity().model_copy(
            update={
                "organization_id": organization,
                "episode_id": f"episode-{count}",
                "rollout_id": f"rollout-{count}",
            }
        )
        source = PackageInput(
            identity=ident,
            source_sha256="1" * 64,
            processor_version="metadata-fixture/1",
            configuration_sha256="2" * 64,
            schema_snapshot_id=schema_id,
            robot_id=robot_id,
            required_features=("observation.state", "observation.recording_time"),
        )
        output = OutputContract(
            frequency_hz=15,
            time_source="FRAME_TIMESTAMP",
            features={
                "observation.state": {
                    "role": "state",
                    "dtype": "float32",
                    "shape": [2],
                    "names": ["a", "b"],
                    "units": ["rad", "rad"],
                },
                "observation.recording_time": {
                    "role": "timestamp",
                    "dtype": "float64",
                    "shape": [1],
                    "names": ["seconds"],
                },
            },
        )
        root = tmp_path / f"source-{count}"
        rows = [
            StepRecord(
                rollout_id=ident.rollout_id,
                step_index=i,
                timestamp_ns=round(i * 1e9 / 15),
                modalities={
                    "/observation/state": {
                        value_key: [float(i + count), float(i * 2)],
                        "names": ["a", "b"],
                        "units": ["rad", "rad"],
                    },
                    "observation.recording_time": i / 15 if scalar else [i / 15],
                },
                valid={"/observation/state": i != 1, "observation.recording_time": True},
            )
            for i in range(3)
        ]
        with scoped(source):
            manifest = write_episode(
                root,
                source,
                rows,
                PreparedAssets(ident, "fixture", {}),
                fps=15,
                metadata={
                    "robot_type": robot_type,
                    "task": "capture metadata fixture",
                    **({"capture": {"output": output.model_dump(mode="json")}} if capture else {}),
                },
            )
        data = DataVersionRef(
            identity=ident,
            data_version_id=source.data_version_id,
            version=1,
            content_hash=manifest.content_hash,
            schema_snapshot_id=schema_id,
            package_prefix=source.package_prefix,
            frame_count=3,
        )
        entries = {entry.path: entry for entry in manifest.files}

        @contextmanager
        def file(relative):
            path = root / relative
            assert hashlib.sha256(path.read_bytes()).hexdigest() == entries[relative].sha256
            yield path

        return SimpleNamespace(
            root=root,
            manifest=manifest,
            receipt=CommitReceipt(
                data=data,
                idempotency_key=source.idempotency_key,
                manifest_sha256=manifest.content_hash,
            ),
            file=file,
            document=lambda relative: json.loads((root / relative).read_bytes()),
        )

    return build


def test_cross_format_collection_preserves_values_validity_and_original_metadata(
    source_factory, tmp_path
):
    packages = [
        source_factory(),
        source_factory(),
        source_factory(robot_type="x1", value_key="positions", scalar=True),
    ]
    output = tmp_path / "merged"
    sources = [CollectionSource(p) for p in packages]
    with scoped(packages[0].manifest.input):
        manifest, checksum = write_collection(output, sources, modalities="rgb")
        repeated, repeated_hash = write_collection(
            tmp_path / "reordered", list(reversed(sources)), modalities="rgb"
        )
    assert repeated == manifest and repeated_hash == checksum
    info = json.loads((output / "meta/info.json").read_bytes())
    assert info["robot_type"] == "x1"
    assert info["hc.package_version"] == NORMALIZED_PROCESSOR
    assert info["total_frames"] == 9 and info["total_episodes"] == 3
    assert info["hc.structured_features"]["observation.state"]["value_key"] == "values"
    assert info["hc.source_shapes"]["observation.recording_time"] == [1]
    audit = json.loads((output / info["hc.metadata_normalization"]).read_bytes())
    assert audit["robot_id"] == "robot-1"
    assert {change["field"] for m in audit["members"] for change in m["changes"]} == {
        "robot_type",
        "hc.structured_features.observation.state.value_key",
        "hc.source_shapes.observation.recording_time",
    }
    parents = {p.receipt.data.data_version_id: p for p in packages}
    for member in manifest["members"]:
        parent = parents[member["data"]["data_version_id"]]
        data = pq.read_table(output / member["data_path"])
        original = pq.read_table(parent.root / "data/chunk-000/file-000.parquet")
        for name in original.column_names:
            if name not in {"index", "episode_index", "task_index"}:
                assert data[name].equals(original[name]), name
        assert (output / member["attachments_root"] / "meta/info.json").read_bytes() == (
            parent.root / "meta/info.json"
        ).read_bytes()
        for entry in parent.manifest.files:
            with parent.file(entry.path):
                pass
    table = pq.read_table(output / "data")
    assert table["index"].to_pylist() == list(range(9))
    measured = [
        v
        for v, valid in zip(
            table["observation.state"].to_pylist(),
            table["hc.valid.observation.state"].to_pylist(),
            strict=True,
        )
        if valid
    ]
    stats = json.loads((output / "meta/stats.json").read_bytes())
    np.testing.assert_allclose(stats["observation.state"]["mean"], np.mean(measured, axis=0))
    assert [p.document("meta/info.json")["robot_type"] for p in packages] == [
        "unknown",
        "unknown",
        "x1",
    ]


@pytest.mark.parametrize(
    "change",
    [
        {"robot_type": "x2"},
        {"robot_type": "unknown", "robot_id": "other-robot"},
        {"robot_type": "unknown", "robot_id": "unknown"},
        {"robot_type": "unknown", "schema_id": "other-schema"},
        {"robot_type": "unknown", "capture": False},
    ],
)
def test_metadata_reconciliation_requires_same_robot_and_capture_evidence(
    source_factory, tmp_path, change
):
    first = source_factory(robot_type="x1")
    second = source_factory(**change)
    with scoped(first.manifest.input), pytest.raises(CollectionIncompatible):
        write_collection(
            tmp_path / "rejected",
            [CollectionSource(first), CollectionSource(second)],
            modalities="rgb",
        )
    assert not (tmp_path / "rejected").exists()


@pytest.mark.parametrize(
    "field", ["fps", "units", "names", "shape", "dtype", "capture", "presence", "encoding"]
)
def test_metadata_reconciliation_still_rejects_semantic_differences(
    source_factory, tmp_path, field
):
    first = source_factory(robot_type="x1", value_key="positions", scalar=True)
    second = source_factory()
    original = second.document

    def document(path):
        info = copy.deepcopy(original(path))
        if path == "meta/info.json":
            if field in {"units", "names", "shape", "dtype"}:
                info["features"]["observation.state"][field] = {
                    "units": ["deg", "deg"],
                    "names": ["b", "a"],
                    "shape": [3],
                    "dtype": "float64",
                }[field]
            elif field == "fps":
                info["fps"] = 30
            elif field == "capture":
                info["hc.capture_contract"]["time_source"] = "OTHER_CLOCK"
            elif field == "presence":
                info["hc.capture_presence"]["observation.state"] = False
            else:
                info["hc.structured_features"]["observation.state"]["value_key"] = "velocities"
        return info

    second.document = document
    with scoped(first.manifest.input), pytest.raises(CollectionIncompatible):
        write_collection(
            tmp_path / "rejected",
            [CollectionSource(first), CollectionSource(second)],
            modalities="rgb",
        )
    assert not (tmp_path / "rejected").exists()


def test_historical_processor_keeps_strict_contract_and_identical_bytes(source_factory, tmp_path):
    first, second = source_factory(), source_factory()
    sources = [CollectionSource(first), CollectionSource(second)]
    with scoped(first.manifest.input):
        old, old_hash = write_collection(
            tmp_path / "old", sources, modalities="rgb", processor_version=PROCESSOR
        )
        new, new_hash = write_collection(tmp_path / "new", sources, modalities="rgb")
        assert old == new and old_hash == new_hash
        assert old["format"] == PROCESSOR
        third = source_factory(robot_type="x1", value_key="positions", scalar=True)
        with pytest.raises(CollectionIncompatible, match="robot_type"):
            write_collection(
                tmp_path / "old-rejects",
                [*sources, CollectionSource(third)],
                modalities="rgb",
                processor_version=PROCESSOR,
            )


@pytest.mark.integration
def test_normalized_job_freezes_replays_and_rejects_changed_contract(
    services, source_factory, tmp_path, monkeypatch
):
    repository, storage = services
    packages = [
        source_factory(),
        source_factory(robot_type="x1", value_key="positions", scalar=True),
    ]
    with scoped(packages[0].manifest.input):
        service = EpisodePackageService(repository.connections, storage, tmp_path / "service")
        try:
            ident = packages[0].manifest.input.identity
            with repository.transaction() as cursor:
                cursor.execute(
                    "INSERT INTO registry.organization_projects "
                    "(organization_id, project_id, display_name) VALUES (%s, %s, %s)",
                    (ident.organization_id, ident.project_id, "Collection metadata fixture"),
                )
            refs = [service.committer.commit(p.root, p.manifest.input).data for p in packages]
            auth = auth_for(refs[0])
            auth = replace(auth, capabilities=auth.capabilities | {"export.create"})
            request = RequestCollectionDelivery(
                members=tuple({"data": ref} for ref in refs), modalities="rgb"
            )
            delivery = service.deliveries
            job = delivery.request_collection(request, "normalize", auth)
            assert job.plan.processor_version == NORMALIZED_PROCESSOR
            restored = CollectionDeliveryPlan.model_validate_json(job.plan.model_dump_json())
            assert restored == job.plan and restored.job_id == job.job_id
            assert len(delivery.collection.sources(restored)) == 2
            with pytest.raises(PackageConflict, match="contract differs"):
                delivery.collection.sources(
                    restored.model_copy(update={"contract_sha256": "0" * 64})
                )
            with pytest.raises(CollectionIncompatible):
                delivery.collection.sources(
                    restored.model_copy(update={"processor_version": PROCESSOR})
                )
            with monkeypatch.context() as fault:
                fault.setattr(
                    delivery, "_upload", lambda *a, **kw: (_ for _ in ()).throw(OSError("fixture"))
                )
                run_job(delivery, job)
            failed = delivery.status(job.job_id, auth)
            assert failed.status == "FAILED"
            retry = delivery.command(job.job_id, 1, auth, retry=True)
            assert retry.plan == restored
            run_job(delivery, retry)
            ready = delivery.status(job.job_id, auth)
            assert ready.status == "READY", ready
            assert ready.artifact_sha256 == failed.artifact_sha256
            row = delivery.repository.get_job(job.job_id)
            archive = b"".join(storage.read_chunks(row["artifact_key"]))
            assert hashlib.sha256(archive).hexdigest() == ready.artifact_sha256
            with zipfile.ZipFile(io.BytesIO(archive)) as z:
                assert json.loads(z.read("collection/meta/info.json"))["robot_type"] == "x1"
                assert json.loads(z.read("delivery.json"))["plan"] == restored.model_dump(
                    mode="json"
                )
            reordered = request.model_copy(update={"members": tuple(reversed(request.members))})
            assert delivery.request_collection(reordered, "reordered", auth) == ready
        finally:
            service.close()
