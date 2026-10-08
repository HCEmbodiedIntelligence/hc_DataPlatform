"""Production settings fail closed; supplied settings are isolated per command."""

from pathlib import Path

import pytest

from hc_data_platform.backup.composition import BackupInfrastructure
from hc_data_platform.backup.whole import WholeBackupError
from tests.backup.test_backup_whole import _fixtures


@pytest.mark.parametrize("profile", [None, "invalid", "production"])
def test_production_missing_dependencies_never_fall_back_to_local(tmp_path: Path, profile):
    plan, *_ = _fixtures(tmp_path)
    settings = {} if profile is None else {"HC_BACKUP_RUNTIME_PROFILE": profile}
    infrastructure = BackupInfrastructure(settings)
    for factory in [infrastructure.signer, infrastructure.repository]:
        with pytest.raises(WholeBackupError) as caught:
            factory(plan)
        assert caught.value.code == "BACKUP_WHOLE_CONFIGURATION_INVALID"


def test_settings_snapshot_does_not_follow_mutable_environment(tmp_path: Path):
    plan, *_ = _fixtures(tmp_path)
    key = tmp_path / "hc-migration-unit001-signing.key"
    key.write_bytes(bytes(range(32)))
    key.chmod(0o600)
    settings = {
        "HC_BACKUP_RUNTIME_PROFILE": "compose_functional",
        "HC_BACKUP_FUNCTIONAL_RUN_ID": "unit001",
        "HC_BACKUP_FUNCTIONAL_SIGNING_KEY_FILE": str(key),
    }
    infrastructure = BackupInfrastructure(settings)
    settings.clear()
    signer = infrastructure.signer(plan)
    signer.public_key.verify(signer.sign(b"isolated settings"), b"isolated settings")
