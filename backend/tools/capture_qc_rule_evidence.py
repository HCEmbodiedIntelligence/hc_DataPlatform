"""Read actual isolated service output; do not manufacture missing ingress evidence."""

import argparse
import json
from collections import Counter
from hashlib import sha256
from pathlib import Path

from hc_data_platform.episode_store.models import PackageManifest
from hc_data_platform.quality.evidence import QcRuleEvidence
from hc_data_platform.quality.models import QcReportV1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    results = []
    for ingress, directory in (
        ("mcap", "mcap-package"),
        ("continuous_recording", "continuous-package"),
        ("native_lerobot", "native-package-offline"),
    ):
        candidates = sorted(
            (args.root / directory).glob("**/hc/quality_rules.json"),
            key=lambda p: p.stat().st_mtime,
        )
        if not candidates:
            raise ValueError(f"missing actual rule evidence for {ingress}")
        path = candidates[-1]
        package = path.parent.parent
        manifest_path = package / "hc/manifest.json"
        if not manifest_path.exists():
            manifest_path = package.parent / "manifest.json"
        manifest = PackageManifest.model_validate_json(manifest_path.read_bytes())
        evidence = QcRuleEvidence.model_validate_json(path.read_bytes())
        raw = json.loads((package / "hc/quality.json").read_bytes())
        report = QcReportV1.model_validate(raw.get("quality_report", raw))
        evidence.validate_report(report)
        if (
            evidence.source_sha256 != manifest.input.source_sha256
            or evidence.rollout_id != manifest.input.identity.rollout_id
        ):
            raise ValueError("ingress evidence differs from the package source")
        for name in ("hc/quality.json", "hc/quality_rules.json"):
            entry = next(item for item in manifest.files if item.path == name)
            body = (package / name).read_bytes()
            if len(body) != entry.size or sha256(body).hexdigest() != entry.sha256:
                raise ValueError("copied QC file differs from its committed manifest")
        results.append(
            {
                "ingress": ingress,
                "package": str(package),
                "data_version_id": manifest.input.data_version_id,
                "manifest_sha256": manifest.content_hash,
                "source_sha256": manifest.input.source_sha256,
                "report_sha256": report.content_sha256,
                "evidence_sha256": evidence.content_sha256,
                "frame_count": manifest.frame_count,
                "qc_status": report.status.value,
                "rule_results": dict(
                    sorted(Counter(rule.status for rule in evidence.rules).items())
                ),
                "input_mode": evidence.input_mode,
                "reason_codes": sorted({rule.reason for rule in evidence.rules if rule.reason}),
            }
        )
    args.output.write_text(
        json.dumps(
            {
                "scope": "actual isolated service outputs using synthetic recordings",
                "entrances": results,
                "real_recording": False,
                "human_acceptance": False,
            },
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
