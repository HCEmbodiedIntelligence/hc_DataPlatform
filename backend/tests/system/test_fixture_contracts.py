from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, cast

import pytest

from hc_data_platform.quality import QualityEngine, QualityInputV1, QualityProfileV1
from hc_data_platform.verification import FakeDecoderProbe, McapVerifier
from hc_data_platform.verification.ports import FakeObjectStorage

FIXTURES = Path(__file__).parents[1] / "fixtures"


def _load_json(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def test_fixture_manifest_hashes_every_golden_input() -> None:
    manifest = _load_json(FIXTURES / "manifest.json")
    for section in ("mcap", "structured"):
        for entry in manifest[section]:
            payload = (FIXTURES / entry["path"]).read_bytes()
            assert len(payload) == entry["size"]
            assert hashlib.sha256(payload).hexdigest() == entry["sha256"]


def test_legal_golden_mcap_is_readable_by_the_official_sdk() -> None:
    reader_module = pytest.importorskip("mcap.reader")
    with (FIXTURES / "mcap" / "legal.mcap").open("rb") as stream:
        messages = tuple(reader_module.make_reader(stream).iter_messages())

    assert len(messages) == 120
    assert {channel.topic for _schema, channel, _message in messages} == {
        "/camera/front/image",
        "/joint_states",
        "/action",
        "/points",
    }


@pytest.mark.parametrize(
    "entry",
    _load_json(FIXTURES / "manifest.json")["mcap"],
    ids=lambda entry: Path(entry["path"]).stem,
)
def test_golden_mcap_has_expected_verification_outcome(entry: dict[str, Any]) -> None:
    payload = (FIXTURES / entry["path"]).read_bytes()
    object_key = f"raw/fixtures/{Path(entry['path']).name}"
    report = McapVerifier(
        FakeObjectStorage({object_key: payload}),
        FakeDecoderProbe(),
    ).verify(
        rollout_id=Path(entry["path"]).stem,
        object_key=object_key,
        source_sha256=hashlib.sha256(payload).hexdigest(),
        required_topics=set(entry["required_topics"]),
        known_optional_topics=set(),
    )

    assert report.status.value == entry["expected_status"]
    actual_codes = {finding.code.value for finding in report.findings}
    assert set(entry["expected_codes"]) <= actual_codes


@pytest.mark.parametrize(
    "path",
    sorted((FIXTURES / "structured").glob("*.json")),
    ids=lambda path: path.stem,
)
def test_structured_quality_fixture_has_expected_outcome(path: Path) -> None:
    case = _load_json(path)
    report = QualityEngine().evaluate(
        QualityInputV1.model_validate(case["input"]),
        QualityProfileV1.model_validate(case["profile"]),
    )

    assert report.status.value == case["expected"]["status"]
    actual_codes = {finding.code.value for finding in report.findings}
    assert set(case["expected"]["codes"]) <= actual_codes
