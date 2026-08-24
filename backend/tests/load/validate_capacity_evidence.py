"""Validate one production capacity artifact without echoing its measured payload."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from hc_data_platform.core.capacity_evidence import validate_capacity_evidence


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    arguments = parser.parse_args()
    try:
        document = json.loads(arguments.artifact.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "FAIL", "errors": [type(error).__name__]}))
        return 1
    validation = validate_capacity_evidence(document)
    print(
        json.dumps(
            {
                "status": "PASS" if validation.accepted else "FAIL",
                "errors": list(validation.errors),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if validation.accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
