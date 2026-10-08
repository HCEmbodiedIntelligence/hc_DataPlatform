"""Read exact pre-change evidence after expanding the current rule vocabulary."""

import hashlib
import json
from enum import Enum
from pathlib import Path

import pytest

from hc_data_platform.capabilities.registry import ImplementationUnavailable
from hc_data_platform.quality import QualityCode, QualityEngine
from hc_data_platform.quality import evidence as evidence_module
from hc_data_platform.quality.evidence import QcEvaluation
from hc_data_platform.quality.rule_sets import (
    ARCHIVED_RULE_SETS,
    BASE_RULE_CODES,
    EVIDENCE_VERSION,
    EvidenceRuleSet,
    EvidenceRuleSets,
)
from tests.quality.test_quality import _input, _profile

FIXTURES = Path(__file__).with_name("fixtures")


@pytest.mark.parametrize("version", ["be06-qc/1", "be06-qc/2", "be06-qc/3"])
def test_archived_bytes_hashes_and_new_execution_are_identical(version, monkeypatch):
    index = json.loads((FIXTURES / "index.json").read_text())
    name = "archived-" + version.replace("/", "-") + ".json"
    body = (FIXTURES / name).read_bytes()
    assert hashlib.sha256(body).hexdigest() == index["files"][name]["sha256"]
    extended = Enum(
        "ExtendedQualityCode",
        {**{c.name: c.value for c in QualityCode}, "FUTURE_DETECTOR": "QC_FUTURE_DETECTOR"},
        type=str,
    )
    monkeypatch.setattr(evidence_module, "QualityCode", extended)
    restored = QcEvaluation.model_validate_json(body)
    assert restored.model_dump_json().encode() == body
    assert restored.report.content_sha256 == index["files"][name]["report_sha256"]
    assert restored.evidence.content_sha256 == index["files"][name]["evidence_sha256"]
    current = QualityEngine().evaluate_with_evidence(_input(), _profile(engine_version=version))
    assert current.model_dump_json().encode() == body


def test_rule_set_extension_selects_exact_version_and_does_not_reinterpret_history():
    new = EvidenceRuleSet("extension/1", BASE_RULE_CODES | {"QC_EXTENSION"})
    registry = EvidenceRuleSets((*ARCHIVED_RULE_SETS, new))
    assert registry.required(EVIDENCE_VERSION, "extension/1") == new.codes
    for old in ARCHIVED_RULE_SETS:
        assert registry.required(EVIDENCE_VERSION, old.engine_version) == old.codes
    with pytest.raises(ValueError, match="duplicate implementation"):
        EvidenceRuleSets((*ARCHIVED_RULE_SETS, new, new))
    with pytest.raises(ImplementationUnavailable):
        registry.required(EVIDENCE_VERSION, "extension/missing")
    with pytest.raises(ValueError, match="unsupported evidence format"):
        registry.required("future-format/1", "be06-qc/1")


def test_unknown_historical_engine_keeps_explicit_unsupported_evidence():
    evaluation = QualityEngine().evaluate_with_evidence(
        _input(), _profile(engine_version="historical-unknown/1")
    )
    assert {r.code.value for r in evaluation.evidence.rules} == BASE_RULE_CODES
    assert all(r.reason == "UNSUPPORTED_ENGINE_EVIDENCE" for r in evaluation.evidence.rules)
    assert all(r.status == "skip" for r in evaluation.evidence.rules)
