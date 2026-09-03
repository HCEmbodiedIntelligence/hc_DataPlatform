"""Verify runtime metric presence and bounded-cardinality label contracts."""

from __future__ import annotations

import argparse
import json
import re
import urllib.request
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

REQUIRED_LABELS = {
    "hc_data_upload_backlog": {"queue"},
    "hc_data_workflow_failures_total": {"workflow_kind", "error_code"},
    "hc_data_qc_outcomes_total": {"outcome", "profile_id"},
    "hc_data_lance_commits_total": {"outcome"},
    "hc_data_transcode_duration_seconds": {"outcome", "view_mode"},
    "hc_data_exports_total": {"outcome", "format"},
    "hc_platform_http_requests_total": {"route", "method", "status_class", "status_code"},
    "hc_platform_http_request_duration_seconds": {"route", "method"},
}
REQUIRED_METRICS = set(REQUIRED_LABELS)
FORBIDDEN_LABELS = {
    "project_id",
    "user_id",
    "object_key",
    "resource_id",
    "workflow_id",
    "request_id",
}
SAMPLE = re.compile(r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(?P<labels>[^}]*)\})?\s+")
LABEL = re.compile(r'(?P<name>[a-zA-Z_][a-zA-Z0-9_]*)="(?:[^"\\]|\\.)*"')


def verify(lines: Iterable[str]) -> dict[str, object]:
    labels_by_metric: dict[str, list[set[str]]] = defaultdict(list)
    for line in lines:
        if not line or line.startswith("#"):
            continue
        match = SAMPLE.match(line)
        if match is None:
            continue
        name = match.group("name")
        base_name = name
        for suffix in ("_bucket", "_count", "_sum", "_created"):
            if name.endswith(suffix):
                base_name = name[: -len(suffix)]
                break
        if base_name not in REQUIRED_METRICS:
            continue
        labels_by_metric[base_name].append(
            {item.group("name") for item in LABEL.finditer(match.group("labels") or "")}
        )
    failures = []
    for metric in sorted(REQUIRED_METRICS):
        samples = labels_by_metric.get(metric, [])
        if not samples:
            failures.append(f"{metric}: no runtime samples")
            continue
        if not any(labels >= REQUIRED_LABELS[metric] for labels in samples):
            failures.append(
                f"{metric}: no sample contains required bounded labels "
                f"{sorted(REQUIRED_LABELS[metric])}"
            )
        leaked_labels = sorted(set().union(*samples).intersection(FORBIDDEN_LABELS))
        if leaked_labels:
            failures.append(f"{metric}: forbidden high-cardinality labels {leaked_labels}")
    return {
        "passed": not failures,
        "metrics_observed": sorted(labels_by_metric),
        "failures": failures,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--file", type=Path)
    source.add_argument("--url")
    args = parser.parse_args()
    if args.file is not None:
        lines = args.file.read_text(encoding="utf-8").splitlines()
    else:
        with urllib.request.urlopen(args.url, timeout=15) as response:
            lines = response.read().decode("utf-8").splitlines()
    result = verify(lines)
    print(json.dumps(result, indent=2, sort_keys=True))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
