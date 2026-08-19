from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path

from tests.gates.sensitive_artifact_scan import (
    TOOL_VERSION,
    report,
    sanitize_file,
    scan_artifacts,
)


def test_scanner_reports_only_relative_path_category_and_count_without_echoing_secret(
    tmp_path: Path,
) -> None:
    secret = "hcs_this_exact_session_value_must_never_be_reported"
    (tmp_path / "clean.log").write_text("request failed with code=DENIED\n", encoding="utf-8")
    (tmp_path / "dirty.xml").write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<testsuite name="security" tests="1" failures="1">
  <testcase classname="tests.security" name="test_failure">
    <failure message="safe failure">Traceback (most recent call last):
  File "/srv/app/tests/test_security.py", line 10, in test_failure
RuntimeError: postgresql://service:database-password@db.internal/platform
{"details":{"signed_url":"https://objects.invalid/raw/private?X-Amz-Signature=private-signature-value","object_key":"raw/project-a/private.mcap"}}
Authorization: Bearer hcs_this_exact_session_value_must_never_be_reported</failure>
  </testcase>
</testsuite>
""",
        encoding="utf-8",
    )
    (tmp_path / "compose.log").write_text(
        "HC_OBJECT_STORE_SECRET_KEY=compose-secret-value\n"
        "Cookie: session=hcs_cookie_value_must_not_escape\n"
        "manifest_uri='s3://private-bucket/raw/private.mcap'\n",
        encoding="utf-8",
    )

    scanned, findings = scan_artifacts(tmp_path)
    serialized = json.dumps(report(tmp_path), sort_keys=True)

    assert scanned == 3
    assert {finding.path for finding in findings} == {"compose.log", "dirty.xml"}
    assert {
        "assigned_secret",
        "bearer_token",
        "platform_session",
        "signed_url",
        "credential_in_dsn",
        "internal_traceback",
        "internal_stack_frame",
        "internal_failure_detail",
        "object_path_field",
        "object_locator",
        "cookie_header",
    } <= {finding.category for finding in findings}
    assert all(finding.count >= 1 for finding in findings)
    assert secret not in serialized
    assert "database-password" not in serialized
    assert "private-signature-value" not in serialized
    assert "raw/project-a/private.mcap" not in serialized
    assert set(json.loads(serialized)["findings"][0]) == {"path", "category", "count"}


def test_junit_redaction_preserves_structure_and_records_hash_audit(tmp_path: Path) -> None:
    report_path = tmp_path / "public-security-expanded.xml"
    original = """<?xml version="1.0" encoding="utf-8"?>
<testsuites tests="2" failures="1">
  <testsuite name="public-security" tests="2" failures="1">
    <testcase classname="tests.security" name="passed" />
    <testcase classname="tests.security" name="failed">
      <failure message="postgresql://user:private-password@db.internal/platform">
Traceback (most recent call last):
  File "/workspace/tests/test_public.py", line 7, in failed
RuntimeError: s3://private-bucket/raw/manifest.json</failure>
    </testcase>
  </testsuite>
</testsuites>
"""
    report_path.write_text(original, encoding="utf-8")
    before_hash = hashlib.sha256(report_path.read_bytes()).hexdigest()

    audit_path = sanitize_file(report_path)

    assert audit_path == tmp_path / "public-security-expanded.xml.redaction.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    assert audit["tool_version"] == TOOL_VERSION
    assert audit["original_filename"] == report_path.name
    assert audit["pre_redaction_sha256"] == before_hash
    assert audit["post_redaction_sha256"] == hashlib.sha256(report_path.read_bytes()).hexdigest()
    assert audit["pre_redaction_sha256"] != audit["post_redaction_sha256"]
    assert audit["finding_categories"]["credential_in_dsn"] == 1
    assert audit["finding_categories"]["internal_failure_detail"] == 1
    assert audit["plaintext_backup_retained"] is False
    assert audit["structure_before"] == audit["structure_after"]

    root = ET.fromstring(report_path.read_bytes())
    assert len(root.findall(".//testcase")) == 2
    assert len(root.findall(".//failure")) == 1
    assert root.find(".//failure").text == "[REDACTED:internal_failure_detail]"  # type: ignore[union-attr]
    assert report(tmp_path)["finding_count"] == 0
    assert not tuple(tmp_path.glob("*.bak"))


def test_scanner_passes_redacted_artifacts(tmp_path: Path) -> None:
    (tmp_path / "report.json").write_text(
        json.dumps(
            {
                "status": "FAIL",
                "credential": "[REDACTED:assigned_secret]",
                "object_path": "[REDACTED:object_path_field]",
                "error_code": "INTERNAL_SERVER_ERROR",
            }
        ),
        encoding="utf-8",
    )
    result = report(tmp_path)
    assert result == {
        "status": "PASS",
        "scanned_files": 1,
        "finding_count": 0,
        "findings": [],
    }


def test_gate_runner_redacts_before_write_and_scans_after_every_gate() -> None:
    runner = (Path(__file__).resolve().parents[3] / "scripts/first_wave_gate.py").read_text(
        encoding="utf-8"
    )

    assert runner.index('safe_output = redact_text(output or "")') < runner.index(
        "log_path.write_text(safe_output"
    )
    artifact_function = runner.split("def artifact_gate()", maxsplit=1)[1].split(
        "def parse_args()", maxsplit=1
    )[0]
    assert artifact_function.index("sanitize_directory(ARTIFACT_ROOT)") < artifact_function.index(
        '"artifact-scan"'
    )
    main_function = runner.split("def main()", maxsplit=1)[1]
    assert "except BaseException as exc" in main_function
    assert 'if gate != "artifact"' in main_function
    assert "artifact_gate()" in main_function
