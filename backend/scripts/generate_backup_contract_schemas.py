"""Generate or verify committed JSON Schemas for backup trust contracts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pydantic import BaseModel

from hc_data_platform.backup.contracts import (
    BackupManifestV1,
    RestoreTargetV1,
    SignatureEnvelopeV1,
)
from hc_data_platform.backup.planned_migration import (
    MigrationCheckpointV1,
    MigrationCutoverApprovalV1,
    MigrationObservationV1,
    PlannedMigrationPlanV1,
)
from hc_data_platform.backup.restore import RestorePlanRequestV1, RestorePlanV1
from hc_data_platform.backup.restore_execution import (
    RestoreApprovalV1,
    RestoreExecutionCheckpointV1,
)
from hc_data_platform.backup.restore_reconciliation import RestoreReconciliationReportV1
from hc_data_platform.backup.whole import WholeBackupChecksumsV1, WholeBackupPlanV1

ROOT = Path(__file__).resolve().parents[2]
CONTRACT_DIRECTORY = ROOT / "docs/architecture/contracts"
SCHEMAS: dict[str, type[BaseModel]] = {
    "hc-platform-backup-v1.schema.json": BackupManifestV1,
    "hc-platform-restore-target-v1.schema.json": RestoreTargetV1,
    "hc-platform-signature-v1.schema.json": SignatureEnvelopeV1,
    "hc-whole-backup-plan-v1.schema.json": WholeBackupPlanV1,
    "hc-platform-checksums-v1.schema.json": WholeBackupChecksumsV1,
    "hc-platform-restore-plan-request-v1.schema.json": RestorePlanRequestV1,
    "hc-platform-restore-plan-v1.schema.json": RestorePlanV1,
    "hc-platform-restore-approval-v1.schema.json": RestoreApprovalV1,
    "hc-platform-restore-checkpoint-v1.schema.json": RestoreExecutionCheckpointV1,
    "hc-platform-restore-reconciliation-report-v1.schema.json": (RestoreReconciliationReportV1),
    "hc-platform-planned-migration-v1.schema.json": PlannedMigrationPlanV1,
    "hc-platform-migration-cutover-approval-v1.schema.json": MigrationCutoverApprovalV1,
    "hc-platform-migration-observation-v1.schema.json": MigrationObservationV1,
    "hc-platform-migration-checkpoint-v1.schema.json": MigrationCheckpointV1,
}


def _render(model: type[BaseModel]) -> str:
    return (
        json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    stale: list[str] = []
    for filename, model in SCHEMAS.items():
        path = CONTRACT_DIRECTORY / filename
        rendered = _render(model)
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != rendered:
                stale.append(str(path.relative_to(ROOT)))
        else:
            path.write_text(rendered, encoding="utf-8")
    if stale:
        parser.error("stale generated schema(s): " + ", ".join(stale))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
