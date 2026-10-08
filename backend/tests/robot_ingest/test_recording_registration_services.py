"""Verified Raw registration enlists in the source job's real transaction."""

from dataclasses import replace
from uuid import uuid4, uuid5

import pytest

from hc_data_platform.continuous_recordings.registration import (
    PostgresRecordingRegistration,
    RecordingRegistrationConflict,
    VerifiedRecordingFacts,
)
from hc_data_platform.robot_ingest import raw_recording
from hc_data_platform.robot_ingest.recording_bridge import RecordingOutboxHandler, result
from tests.robot_ingest.test_raw_derivation_services import uploaded_raw  # noqa: F401

pytest_plugins = ["tests.episode_store.test_commit_services"]


@pytest.mark.integration
def test_registered_target_and_source_completion_rollback_together(uploaded_raw, monkeypatch):  # noqa: F811
    repository, storage, upload, _, _, event = uploaded_raw
    registration = PostgresRecordingRegistration()
    captured = []

    class FailAfterRegistration:
        find_upload = staticmethod(registration.find_upload)
        object_keys = staticmethod(registration.object_keys)

        def register(self, cursor, facts):
            captured.append(facts)
            registration.register(cursor, facts)
            raise InterruptedError("after target rows before source job completion")

    handler = RecordingOutboxHandler(
        repository.connections, storage, registrations=FailAfterRegistration()
    )
    with pytest.raises(InterruptedError):
        handler.adopt(event)
    facts = captured[0]
    with repository.transaction() as cursor:
        for table in ("recording_uploads", "recording_upload_assets", "continuous_recordings"):
            # Fixed table names from this test, no caller-controlled identifiers.
            cursor.execute(
                f"SELECT count(*) FROM ingest.{table} WHERE organization_id=%s",
                (upload.target.organization_id,),
            )
            assert cursor.fetchone()[0] == 0
        cursor.execute(
            "SELECT status FROM ingest.raw_ingest_jobs WHERE raw_source_id=%s",
            (upload.raw_source_id,),
        )
        assert cursor.fetchone()[0] != "SUCCEEDED"

    def no_reencode(*args, **kwargs):
        raise AssertionError("retry must reuse immutable derivation objects")

    monkeypatch.setattr(raw_recording, "prepare", no_reencode)
    handler = RecordingOutboxHandler(repository.connections, storage, registrations=registration)
    handler.adopt(event)
    handler.adopt(event)
    assert result(repository.connections, upload)["status"] == "AWAITING_SLICE"
    with repository.transaction() as cursor:
        cursor.execute(
            "SELECT status FROM ingest.raw_ingest_jobs WHERE raw_source_id=%s",
            (upload.raw_source_id,),
        )
        assert cursor.fetchone()[0] == "SUCCEEDED"

    # Same manifest with a changed physical object is an immutable identity conflict.
    changed = replace(
        facts,
        assets=(
            facts.assets[0].model_copy(update={"object_key": "foreign/object"}),
            *facts.assets[1:],
        ),
    )
    with (
        pytest.raises(RecordingRegistrationConflict, match="asset identity"),
        repository.transaction() as cursor,
    ):
        registration.register(cursor, changed)

    # Another upload cannot claim the same recording; its target inserts roll back.
    other_id = uuid4()
    other = VerifiedRecordingFacts(
        facts.upload.model_copy(update={"upload_id": other_id}),
        tuple(
            a.model_copy(
                update={"upload_id": other_id, "asset_id": uuid5(other_id, a.manifest.path)}
            )
            for a in facts.assets
        ),
    )
    with (
        pytest.raises(RecordingRegistrationConflict, match="owned by another upload"),
        repository.transaction() as cursor,
    ):
        registration.register(cursor, other)
    with repository.transaction() as cursor:
        cursor.execute(
            "SELECT count(*) FROM ingest.recording_uploads WHERE upload_id=%s", (other_id,)
        )
        assert cursor.fetchone()[0] == 0
