"""Fail-closed scanner for test reports and logs without echoing matched secrets."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts.artifact_security import (  # noqa: E402
    TOOL_VERSION,
    inspect_bytes,
    sanitize_directory,
    sanitize_file,
)

__all__ = (
    "Finding",
    "TOOL_VERSION",
    "report",
    "sanitize_file",
    "scan_artifacts",
)


@dataclass(frozen=True, slots=True)
class Finding:
    path: str
    category: str
    count: int


BINARY_SUFFIXES = frozenset(
    {".gif", ".gz", ".ico", ".jpeg", ".jpg", ".pdf", ".png", ".webp", ".zip"}
)


def scan_artifacts(root: Path) -> tuple[int, tuple[Finding, ...]]:
    findings: list[Finding] = []
    scanned = 0
    if not root.is_dir():
        raise ValueError(f"artifact directory does not exist: {root}")
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() in BINARY_SUFFIXES:
            continue
        data = path.read_bytes()
        if b"\0" in data[:4096]:
            continue
        scanned += 1
        counts, _ = inspect_bytes(path, data)
        relative = str(path.relative_to(root))
        findings.extend(
            Finding(path=relative, category=category, count=count)
            for category, count in counts.items()
        )
    return scanned, tuple(findings)


def report(root: Path) -> dict[str, object]:
    scanned, findings = scan_artifacts(root)
    return {
        "status": "PASS" if not findings else "FAIL",
        "scanned_files": scanned,
        "finding_count": sum(finding.count for finding in findings),
        # Never include excerpts or matched values in the scanner's own artifact.
        "findings": [asdict(finding) for finding in findings],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact_directory", type=Path)
    parser.add_argument(
        "--sanitize",
        action="store_true",
        help="redact unsafe text in place and write auditable hash sidecars before scanning",
    )
    arguments = parser.parse_args()
    root = arguments.artifact_directory.resolve()
    sanitized = sanitize_directory(root) if arguments.sanitize else ()
    result = report(root)
    result["sanitized_files"] = [str(path.relative_to(root)) for path in sanitized]
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
