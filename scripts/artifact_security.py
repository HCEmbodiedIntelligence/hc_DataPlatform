"""Shared secret-safe artifact redaction used by gate runners and scanners."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

TOOL_VERSION = "hc-artifact-redactor/1.0"
MARKER_TEMPLATE = "[REDACTED:{category}]"

# Patterns deliberately match the whole credential/locator where practical.  Reports and
# logs may contain XML-escaped JSON, so none of these expressions relies on parsing one
# particular serialization format.
PATTERNS: dict[str, re.Pattern[str]] = {
    "private_key": re.compile(
        r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----.*?"
        r"-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
        re.DOTALL,
    ),
    "credential_in_dsn": re.compile(
        r"(?i)\bpostgres(?:ql)?(?:\+asyncpg)?://[^\s\"'<>]+"
    ),
    "bearer_token": re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{12,}"),
    "platform_session": re.compile(r"\bhcs_[A-Za-z0-9_-]{12,}"),
    "signed_url": re.compile(
        r"(?i)https?://[^\s\"'<>]+(?:X-Amz-Signature|X-Amz-Credential|"
        r"X-Goog-Signature|Signature=)[^\s\"'<>]*"
    ),
    "cookie_header": re.compile(r"(?im)^(?:set-cookie|cookie)\s*:\s*[^\r\n]+"),
    "assigned_secret": re.compile(
        r"(?i)(?:password|secret|access[_-]?key|authorization|cookie|token)"
        r"[\s\"']*[:=][\s\"']*[^\s,;\"'<>]{4,}"
    ),
    "object_locator": re.compile(r"(?i)\b(?:s3|minio)://[^\s\"'<>]+"),
    "object_path_field": re.compile(
        r"(?i)[\"']?(?:object_key|object_path|staging_uri|signed_url|manifest_uri|"
        r"manifest_path|raw_key)[\"']?\s*[:=]\s*[\"'](?!\[REDACTED:)[^\"']+[\"']"
    ),
    "internal_traceback": re.compile(r"Traceback \(most recent call last\):"),
    "internal_stack_frame": re.compile(
        r"(?m)^\s*(?:File \"[^\"]+\", line \d+|[^\s].*\.py:\d+: in [^\r\n]+).*$"
    ),
}

_BINARY_SUFFIXES = frozenset(
    {".gif", ".gz", ".ico", ".jpeg", ".jpg", ".pdf", ".png", ".webp", ".zip"}
)
_FAILURE_TAGS = frozenset({"failure", "error"})


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def finding_counts(text: str, *, xml_root: ET.Element | None = None) -> dict[str, int]:
    counts = Counter(
        {
            category: len(tuple(pattern.finditer(text)))
            for category, pattern in PATTERNS.items()
        }
    )
    if xml_root is not None:
        failure_details = sum(
            1
            for element in xml_root.iter()
            if element.tag.rsplit("}", maxsplit=1)[-1] in _FAILURE_TAGS
            and element.text
            and not element.text.strip().startswith("[REDACTED:")
        )
        if failure_details:
            counts["internal_failure_detail"] += failure_details
    return {category: count for category, count in sorted(counts.items()) if count}


def redact_text(text: str) -> str:
    redacted = text
    for category, pattern in PATTERNS.items():
        redacted = pattern.sub(MARKER_TEMPLATE.format(category=category), redacted)
    return redacted


def _parse_xml(data: bytes, path: Path) -> ET.Element | None:
    if path.suffix.lower() != ".xml":
        return None
    try:
        return ET.fromstring(data)
    except ET.ParseError:
        return None


def _structure(root: ET.Element | None) -> dict[str, object] | None:
    if root is None:
        return None
    tags = Counter(element.tag.rsplit("}", maxsplit=1)[-1] for element in root.iter())
    return {
        "root": root.tag.rsplit("}", maxsplit=1)[-1],
        "testsuites": tags["testsuites"] + tags["testsuite"],
        "testcases": tags["testcase"],
        "failures": tags["failure"],
        "errors": tags["error"],
        "skipped": tags["skipped"],
    }


def _redact_xml(root: ET.Element) -> bytes:
    for element in root.iter():
        tag = element.tag.rsplit("}", maxsplit=1)[-1]
        for name, value in tuple(element.attrib.items()):
            element.attrib[name] = redact_text(value)
        if element.text:
            element.text = (
                MARKER_TEMPLATE.format(category="internal_failure_detail")
                if tag in _FAILURE_TAGS
                else redact_text(element.text)
            )
        if element.tail:
            element.tail = redact_text(element.tail)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _atomic_write(path: Path, data: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def inspect_bytes(path: Path, data: bytes) -> tuple[dict[str, int], ET.Element | None]:
    root = _parse_xml(data, path)
    text = data.decode("utf-8", errors="replace")
    return finding_counts(text, xml_root=root), root


def sanitize_file(path: Path) -> Path | None:
    data = path.read_bytes()
    if b"\0" in data[:4096] or path.suffix.lower() in _BINARY_SUFFIXES:
        return None
    before_findings, root = inspect_bytes(path, data)
    if not before_findings:
        return None

    redacted = (
        _redact_xml(root)
        if root is not None
        else redact_text(data.decode("utf-8", errors="replace")).encode("utf-8")
    )
    after_findings, after_root = inspect_bytes(path, redacted)
    if after_findings:
        categories = ", ".join(sorted(after_findings))
        raise RuntimeError(
            f"artifact redaction incomplete for {path.name}: {categories}"
        )

    before_structure = _structure(root)
    after_structure = _structure(after_root)
    if before_structure != after_structure:
        raise RuntimeError(f"artifact structure changed during redaction: {path.name}")

    audit_path = path.with_name(f"{path.name}.redaction.json")
    audit = {
        "tool_version": TOOL_VERSION,
        "original_filename": path.name,
        "pre_redaction_sha256": sha256_bytes(data),
        "post_redaction_sha256": sha256_bytes(redacted),
        "finding_categories": before_findings,
        "structure_before": before_structure,
        "structure_after": after_structure,
        "plaintext_backup_retained": False,
    }
    _atomic_write(path, redacted)
    _atomic_write(
        audit_path,
        (json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
            "utf-8"
        ),
    )
    return audit_path


def sanitize_directory(root: Path) -> tuple[Path, ...]:
    audit_paths: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name.endswith(".redaction.json"):
            continue
        audit_path = sanitize_file(path)
        if audit_path is not None:
            audit_paths.append(audit_path)
    return tuple(audit_paths)
