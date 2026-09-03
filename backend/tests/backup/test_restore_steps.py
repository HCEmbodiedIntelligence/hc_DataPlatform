from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from hc_data_platform.backup.contracts import verify_signed_manifest
from hc_data_platform.backup.objects import ObjectRestoreReport
from hc_data_platform.backup.postgresql import RestoreVerificationReport
from hc_data_platform.backup.repository import RepositoryVerifiedBackup
from hc_data_platform.backup.restore import create_restore_plan
from hc_data_platform.backup.restore_execution import RestoreExecutionError
from hc_data_platform.backup.restore_steps import (
    ObjectsRestoredStep,
    PayloadVerifiedStep,
    PostgreSQLRestoredStep,
    RestorePayloadResolver,
)
from hc_data_platform.backup.whole import assemble_whole_backup, prepare_postgresql_artifact
from tests.backup.test_backup_whole import _evidence, _fixtures
from tests.backup.test_restore_plan import _Inspector, _request


class _Repository:
    def __init__(self, payload: RepositoryVerifiedBackup) -> None:
        self.payload = payload
        self.calls = 0

    def restore_backup_payload(self, *_: object, **__: object) -> RepositoryVerifiedBackup:
        self.calls += 1
        return self.payload


class _PostgresRestore:
    def __init__(self) -> None:
        self.artifact: object | None = None

    def restore_manifest_dump_and_verify(
        self,
        artifact: Any,
        target: Any,
        *,
        staging_directory: Path,
    ) -> RestoreVerificationReport:
        del staging_directory
        self.artifact = artifact
        assert artifact.source_database == "hc_data"
        assert artifact.source_evidence.schema_sha256 == "1" * 64
        assert artifact.source_evidence.constraint_sha256 == "2" * 64
        assert artifact.source_evidence.sequence_sha256 == "3" * 64
        assert artifact.source_evidence.aggregate_sha256 == "4" * 64
        return RestoreVerificationReport(
            target_database=target.database,
            server_major=artifact.server_major,
            aggregate_sha256=artifact.source_evidence.aggregate_sha256,
        )


class _ObjectRestore:
    def __init__(self) -> None:
        self.restore_calls = 0
        self.verify_calls = 0

    def restore(self, *_: object, **__: object) -> ObjectRestoreReport:
        self.restore_calls += 1
        return self._report()

    def verify_external(self, *_: object, **__: object) -> ObjectRestoreReport:
        self.verify_calls += 1
        return self._report()

    @staticmethod
    def _report() -> ObjectRestoreReport:
        return ObjectRestoreReport(
            target_bucket_reference="logical-source-bucket",
            target_prefix="datasets/production",
            object_count=1,
            total_bytes=100_000_000,
            resumed_object_count=1,
            source_content_sha256="7" * 64,
            target_version_set_sha256="8" * 64,
        )


def _resolver(tmp_path: Path) -> tuple[RestorePayloadResolver, _Repository, Any]:
    staging = tmp_path / "staging"
    staging.mkdir(mode=0o700)
    plan_input, postgres, objects, configuration, temporal, signer = _fixtures(staging)
    prepared = prepare_postgresql_artifact(plan_input, postgres, staging_directory=staging)
    assembly = assemble_whole_backup(
        plan_input,
        postgresql_receipt=postgres,
        postgresql_upload=prepared,
        object_receipt=objects,
        configuration_receipt=configuration,
        temporal_receipt=temporal,
        verification=_evidence(plan_input),  # type: ignore[arg-type]
        signer=signer,
        staging_directory=staging,
    )
    verified = verify_signed_manifest(
        assembly.signed_manifest.manifest_bytes,
        assembly.signed_manifest.signature_bytes,
        public_key=signer.public_key,
    )
    request = _request(verified.manifest)
    restore_plan = create_restore_plan(
        verified,
        request,
        inspector=_Inspector(request),
        clock=lambda: request.requested_at,
    )
    artifact_paths = {
        source.logical_path: source.path
        for source in assembly.upload_sources
        if source.logical_path in {artifact.path for artifact in assembly.manifest.artifacts}
    }
    payload = RepositoryVerifiedBackup(
        verified_manifest=verified,
        artifact_paths=artifact_paths,
        manifest_version_id="manifest-version-1",
        signature_version_id="signature-version-1",
    )
    repository = _Repository(payload)
    resolver = RestorePayloadResolver(
        plan=restore_plan,
        request=request,
        repository=repository,  # type: ignore[arg-type]
        public_keys_by_sha256={},
        staging_directory=staging,
        execution_started_at=request.requested_at,
    )
    return resolver, repository, request


def test_payload_step_rebinds_exact_plan_request_and_authenticated_manifest(
    tmp_path: Path,
) -> None:
    resolver, repository, _request_document = _resolver(tmp_path)

    result = PayloadVerifiedStep(resolver).execute(resolver.plan, None)

    assert result.step == "payload_verified"
    assert result.target_mutated is False
    assert repository.calls == 1


def test_payload_step_rejects_substituted_target_prefix_before_domain_mutation(
    tmp_path: Path,
) -> None:
    resolver, _repository, request = _resolver(tmp_path)
    changed = request.model_copy(
        update={"target_object_store_prefix": "rehearsal/substituted-prefix"}
    )
    resolver = RestorePayloadResolver(
        plan=resolver.plan,
        request=changed,
        repository=resolver.repository,
        public_keys_by_sha256=resolver.public_keys_by_sha256,
        staging_directory=resolver.staging_directory,
        execution_started_at=resolver.execution_started_at,
    )

    with pytest.raises(RestoreExecutionError) as captured:
        PayloadVerifiedStep(resolver).execute(resolver.plan, None)
    assert captured.value.code == "RESTORE_EXECUTION_INPUT_MISMATCH"


def test_objects_step_reuse_external_verifies_without_copying(tmp_path: Path) -> None:
    resolver, repository, request = _resolver(tmp_path)
    manifest = repository.payload.verified_manifest.manifest
    reuse_request = request.model_copy(
        update={
            "object_restore_mode": "reuse_external",
            "target_object_store_bucket_reference": manifest.object_store.bucket_reference,
            "target_object_store_prefix": manifest.object_store.prefix.strip("/"),
        }
    )
    reuse_plan = create_restore_plan(
        repository.payload.verified_manifest,
        reuse_request,
        inspector=_Inspector(reuse_request),
        clock=lambda: reuse_request.requested_at,
    )
    reuse_resolver = RestorePayloadResolver(
        plan=reuse_plan,
        request=reuse_request,
        repository=repository,  # type: ignore[arg-type]
        public_keys_by_sha256={},
        staging_directory=resolver.staging_directory,
        execution_started_at=reuse_request.requested_at,
    )
    adapter = _ObjectRestore()

    result = ObjectsRestoredStep(
        reuse_resolver,
        adapter,  # type: ignore[arg-type]
    ).execute(reuse_plan, None)

    assert result.step == "objects_restored"
    assert result.target_mutated is False
    assert adapter.verify_calls == 1
    assert adapter.restore_calls == 0


def test_postgresql_step_reconstructs_signed_content_evidence(tmp_path: Path) -> None:
    resolver, _repository, _request_document = _resolver(tmp_path)
    adapter = _PostgresRestore()

    result = PostgreSQLRestoredStep(
        resolver,
        adapter,  # type: ignore[arg-type]
        target=type(
            "Target",
            (),
            {"database": resolver.request.target_postgresql_database},
        )(),
    ).execute(resolver.plan, None)

    assert result.step == "postgresql_restored"
    assert result.writes_enabled is False
    assert adapter.artifact is not None
