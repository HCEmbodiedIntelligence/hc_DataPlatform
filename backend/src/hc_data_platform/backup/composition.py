"""Shared backup/restore infrastructure, composed from one explicit settings snapshot.

Only command boundaries read process environment. Both commands consume these
public factories; neither imports the other command's private implementation.
Production remains fail-closed and never selects the functional file adapter
unless that profile is explicitly requested.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

from hc_data_platform.backup.contracts import ManifestSigningPort
from hc_data_platform.backup.repository import (
    BackupPayloadRepository,
    FunctionalFileBackupRepository,
    FunctionalFileRepositoryConfig,
    LockedRepositoryConfig,
    S3LockedBackupRepository,
)
from hc_data_platform.backup.signing import (
    FunctionalFileEd25519Signer,
    VaultTransitEd25519Signer,
    VaultTransitSignerConfig,
)
from hc_data_platform.backup.whole import WholeBackupError, WholeBackupPlanV1


class BackupInfrastructure:
    def __init__(self, settings: Mapping[str, str]) -> None:
        self._settings = MappingProxyType(dict(settings))

    def _environment(self, name: str, *, default: str | None = None) -> str:
        value = self._settings.get(name, default)
        if value is None or not value or any(char in value for char in ("\x00", "\r", "\n")):
            raise WholeBackupError(
                "BACKUP_WHOLE_CONFIGURATION_INVALID",
                "a required whole-backup Job setting is absent or invalid",
            )
        return value

    def _integer(self, name: str, *, default: int, minimum: int, maximum: int) -> int:
        try:
            value = int(self._environment(name, default=str(default)))
        except ValueError as exc:
            raise WholeBackupError(
                "BACKUP_WHOLE_CONFIGURATION_INVALID",
                "a whole-backup Job integer setting is invalid",
            ) from exc
        if not minimum <= value <= maximum:
            raise WholeBackupError(
                "BACKUP_WHOLE_CONFIGURATION_INVALID",
                "a whole-backup Job integer setting is outside its bound",
            )
        return value

    def _number(self, name: str, *, default: float, minimum: float, maximum: float) -> float:
        try:
            value = float(self._environment(name, default=str(default)))
        except ValueError as exc:
            raise WholeBackupError(
                "BACKUP_WHOLE_CONFIGURATION_INVALID",
                "a whole-backup Job numeric setting is invalid",
            ) from exc
        if not minimum <= value <= maximum:
            raise WholeBackupError(
                "BACKUP_WHOLE_CONFIGURATION_INVALID",
                "a whole-backup Job numeric setting is outside its bound",
            )
        return value

    def _boolean(self, name: str, *, default: bool) -> bool:
        value = self._environment(name, default="true" if default else "false").lower()
        if value not in {"true", "false"}:
            raise WholeBackupError(
                "BACKUP_WHOLE_CONFIGURATION_INVALID",
                "a whole-backup Job Boolean setting is invalid",
            )
        return value == "true"

    def _runtime_profile(self) -> str:
        profile = self._environment("HC_BACKUP_RUNTIME_PROFILE", default="production")
        if profile not in {"production", "compose_functional"}:
            raise WholeBackupError(
                "BACKUP_WHOLE_CONFIGURATION_INVALID", "the backup runtime profile is invalid"
            )
        return profile

    def repository(self, plan: WholeBackupPlanV1) -> BackupPayloadRepository:
        if self._runtime_profile() == "compose_functional":
            try:
                repository = FunctionalFileBackupRepository(
                    FunctionalFileRepositoryConfig(
                        repository_id=plan.backup_repository.repository_id,
                        provider=plan.backup_repository.provider,
                        bucket_reference=plan.backup_repository.bucket_reference,
                        kms_key_reference=plan.backup_repository.kms_key_reference,
                        retention_until=plan.backup_repository.retention_until,
                        cross_site_replica_reference=(
                            plan.backup_repository.cross_site_replica_reference
                        ),
                        root_directory=Path(
                            self._environment("HC_BACKUP_FUNCTIONAL_REPOSITORY_DIRECTORY")
                        ),
                        run_id=self._environment("HC_BACKUP_FUNCTIONAL_RUN_ID"),
                    )
                )
            except ValueError as exc:
                raise WholeBackupError(
                    "BACKUP_WHOLE_CONFIGURATION_INVALID",
                    "the functional repository configuration is invalid",
                ) from exc
            return repository
        try:
            config = LockedRepositoryConfig(
                repository_id=plan.backup_repository.repository_id,
                provider=plan.backup_repository.provider,
                bucket=self._environment("HC_BACKUP_REPOSITORY_BUCKET"),
                bucket_reference=plan.backup_repository.bucket_reference,
                prefix=self._environment(
                    "HC_BACKUP_REPOSITORY_PREFIX", default="whole-platform-backups"
                ),
                kms_key_reference=plan.backup_repository.kms_key_reference,
                provider_kms_key_id=self._environment("HC_BACKUP_REPOSITORY_KMS_KEY_ID"),
                provider_kms_observed_key_id=self._environment(
                    "HC_BACKUP_REPOSITORY_KMS_OBSERVED_KEY_ID"
                ),
                provider_replica_destination=self._environment(
                    "HC_BACKUP_REPOSITORY_REPLICA_DESTINATION"
                ),
                retention_until=plan.backup_repository.retention_until,
                cross_site_replica_reference=(plan.backup_repository.cross_site_replica_reference),
                multipart_threshold_bytes=self._integer(
                    "HC_BACKUP_REPOSITORY_MULTIPART_THRESHOLD_BYTES",
                    default=64 * 1024 * 1024,
                    minimum=5 * 1024 * 1024,
                    maximum=5 * 1024 * 1024 * 1024,
                ),
                multipart_part_bytes=self._integer(
                    "HC_BACKUP_REPOSITORY_MULTIPART_PART_BYTES",
                    default=64 * 1024 * 1024,
                    minimum=5 * 1024 * 1024,
                    maximum=5 * 1024 * 1024 * 1024,
                ),
                send_small_object_checksum=self._boolean(
                    "HC_BACKUP_REPOSITORY_SEND_SMALL_OBJECT_CHECKSUM",
                    default=True,
                ),
            )
        except ValueError as exc:
            raise WholeBackupError(
                "BACKUP_WHOLE_CONFIGURATION_INVALID",
                "the locked backup repository configuration is invalid",
            ) from exc
        import boto3
        from botocore.config import Config

        endpoint = self._settings.get("HC_BACKUP_REPOSITORY_ENDPOINT")
        if endpoint is not None and any(char in endpoint for char in ("\x00", "\r", "\n")):
            raise WholeBackupError(
                "BACKUP_WHOLE_CONFIGURATION_INVALID",
                "the backup repository endpoint is invalid",
            )
        client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            region_name=self._environment("HC_BACKUP_REPOSITORY_REGION", default="us-east-1"),
            config=Config(
                signature_version="s3v4",
                retries={"max_attempts": 1, "mode": "standard"},
                s3={
                    "addressing_style": self._environment(
                        "HC_BACKUP_REPOSITORY_ADDRESSING_STYLE", default="path"
                    )
                },
            ),
        )
        return S3LockedBackupRepository(client, config)

    def signer(self, plan: WholeBackupPlanV1) -> ManifestSigningPort:
        if self._runtime_profile() == "compose_functional":
            signer = FunctionalFileEd25519Signer(
                Path(self._environment("HC_BACKUP_FUNCTIONAL_SIGNING_KEY_FILE")),
                key_reference=plan.secrets_and_kms.signing_key_reference,
                run_id=self._environment("HC_BACKUP_FUNCTIONAL_RUN_ID"),
            )
            return signer
        return VaultTransitEd25519Signer(
            VaultTransitSignerConfig(
                endpoint_url=self._environment("HC_BACKUP_SIGNER_VAULT_ENDPOINT"),
                token=self._environment("HC_BACKUP_SIGNER_VAULT_TOKEN"),
                provider_reference=self._environment("HC_BACKUP_SIGNER_PROVIDER_REFERENCE"),
                transit_mount=self._environment(
                    "HC_BACKUP_SIGNER_TRANSIT_MOUNT", default="transit"
                ),
                key_name=self._environment("HC_BACKUP_SIGNER_KEY_NAME"),
                key_version=self._integer(
                    "HC_BACKUP_SIGNER_KEY_VERSION",
                    default=0,
                    minimum=1,
                    maximum=2**31 - 1,
                ),
                key_reference=plan.secrets_and_kms.signing_key_reference,
                namespace=self._settings.get("HC_BACKUP_SIGNER_VAULT_NAMESPACE"),
                timeout_seconds=self._number(
                    "HC_BACKUP_SIGNER_TIMEOUT_SECONDS",
                    default=10,
                    minimum=0.1,
                    maximum=120,
                ),
            )
        )
