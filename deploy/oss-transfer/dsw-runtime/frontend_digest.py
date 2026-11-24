"""Identify a staged DSW static frontend or check the digest in its served config.js."""
import argparse
import hashlib
import re
import sys
from pathlib import Path


def bundle_digest(web: Path) -> str:
    index = web / "index.html"
    assets = [path for path in (web / "assets").rglob("*") if path.is_file()]
    if not index.is_file() or index.stat().st_size == 0 or not assets:
        raise ValueError(f"Expected index.html and built assets in {web}")

    manifest = hashlib.sha256()
    for path in sorted([index, *assets]):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest.update(f"{digest}  {path.relative_to(web).as_posix()}\n".encode())
    return "sha256:" + manifest.hexdigest()


def check_config(config: str) -> None:
    # DSW generates a JavaScript object literal with quoted or bare keys.
    values = re.findall(r'(?:^|[,{])\s*["\']?VITE_COMPONENT_IMAGE_DIGEST["\']?\s*:\s*["\']([^"\']*)["\']', config)
    if len(values) != 1 or not re.fullmatch(r"sha256:(?!0{64}$)[0-9a-f]{64}", values[0]):
        raise ValueError("config.js requires one nonzero SHA-256 VITE_COMPONENT_IMAGE_DIGEST")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--bundle", type=Path, help="staged web directory for this release")
    group.add_argument("--check-config", action="store_true", help="read config.js from stdin")
    args = parser.parse_args()
    try:
        if args.check_config:
            check_config(sys.stdin.read())
        else:
            print(bundle_digest(args.bundle))
    except (OSError, ValueError) as error:
        sys.exit(str(error))
