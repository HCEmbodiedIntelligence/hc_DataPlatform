#!/usr/bin/env python3
"""交互式运行 Unitree G1 LeRobot 平台上传工具。"""

from __future__ import annotations

import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent


def _main() -> int:
    sys.path.insert(0, str(REPOSITORY_ROOT / "backend/src"))
    from hc_data_platform.tools.lerobot_platform_upload import main

    return main()


if __name__ == "__main__":
    raise SystemExit(_main())
