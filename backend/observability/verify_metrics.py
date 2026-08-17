"""Verify that a Prometheus exposition contains every BE-12 metric and locator label."""

from __future__ import annotations

import argparse
import json
import re
import urllib.request
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

REQUIRED_DIMENSIONS = {"project_id", "resource_id", "workflow_id"}
REQUIRED_METRICS = {
    "hc_data_upload_backlog",
    "hc_data_workflow_failures_total",
    "hc_data_qc_outcomes_total",
    "hc_data_lance_commits_total",
    "hc_data_transcode_duration_seconds",
    "hc_data_exports_total",
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
        if not any(labels >= REQUIRED_DIMENSIONS for labels in samples):
            failures.append(f"{metric}: no sample contains project_id/resource_id/workflow_id")
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
