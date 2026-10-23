#!/usr/bin/env python3
"""Build a bundled tunnel frontend using the installed dev container dependencies."""

import json
from pathlib import Path
import shutil
import subprocess
import tempfile

REPO = Path(__file__).resolve().parents[2]
STATE = REPO.parent / ".data-platform-tools/cloudflare"
DESTINATION = STATE / "frontend"
COMPOSE = ["bash", str(REPO / "deploy/local/compose.sh"), "current"]


def main():
    container = subprocess.check_output(COMPOSE + ["ps", "-q", "frontend"], text=True).strip()
    if not container:
        raise RuntimeError("Start the local frontend before building its public bundle.")
    program = r'''
const fs = require('node:fs');
const { spawnSync } = require('node:child_process');
const directory = fs.mkdtempSync('/tmp/hc-public-');
try {
  for (const entry of fs.readdirSync('/app')) {
    if (['node_modules', 'dist', '.git', '.vite', '.pnpm-store', 'test-results',
         'playwright-report', 'coverage', '.g1-artifacts'].includes(entry)) continue;
    fs.cpSync('/app/' + entry, directory + '/' + entry, { recursive: true });
  }
  fs.symlinkSync('/app/node_modules', directory + '/node_modules', 'dir');
  const build = spawnSync('pnpm', ['build'], {
    cwd: directory, stdio: ['ignore', 'inherit', 'inherit'],
    env: { ...process.env, NODE_ENV: 'production', VITE_MOCK_MODE: 'off',
      VITE_ROBOT_PLATFORM_BASE_URL: '', VITE_PUBLISH_SOURCEMAPS: 'false' },
  });
  if (build.error) throw build.error;
  if (build.status !== 0) throw new Error('Frontend build failed: ' + build.status);
  console.log('HC_PUBLIC_BUILD_DIRECTORY=' + directory);
} catch (error) {
  fs.rmSync(directory, { recursive: true, force: true });
  throw error;
}
'''
    build = subprocess.run(["docker", "exec", container, "node", "-e", program],
                           capture_output=True, text=True, timeout=300)
    print(build.stdout, end="", flush=True)
    print(build.stderr, end="", flush=True)
    build.check_returncode()
    directory = next(line.split("=", 1)[1] for line in build.stdout.splitlines()
                     if line.startswith("HC_PUBLIC_BUILD_DIRECTORY="))
    if not directory.startswith("/tmp/hc-public-") or "/.." in directory:
        raise RuntimeError("Unexpected build directory")
    STATE.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix="frontend-build-", dir=STATE) as staging:
            subprocess.run(["docker", "cp", f"{container}:{directory}/dist/.", staging], check=True)
            source = Path(staging)
            html = (source / "index.html").read_text()
            if "/src/main.tsx" in html or "/assets/" not in html:
                raise RuntimeError("Expected bundled frontend assets")
            if any(source.rglob("*.map")):
                raise RuntimeError("Public source maps must not be published")
            DESTINATION.mkdir(parents=True, exist_ok=True, mode=0o755)
            # Keep previous hashed chunks so open tabs remain usable during updates.
            # Publish the new index last, after every referenced asset is in place.
            for path in source.iterdir():
                if path.name == "index.html":
                    continue
                target = DESTINATION / path.name
                if path.is_dir():
                    shutil.copytree(path, target, dirs_exist_ok=True)
                else:
                    shutil.copy2(path, target)
            temporary_index = DESTINATION / "index.html.new"
            shutil.copy2(source / "index.html", temporary_index)
            temporary_index.replace(DESTINATION / "index.html")
            summary = {"files": sum(1 for p in source.rglob("*") if p.is_file()),
                       "bytes": sum(p.stat().st_size for p in source.rglob("*") if p.is_file())}
            (STATE / "frontend-build.json").write_text(json.dumps(summary, indent=2) + "\n")
            print("Published public frontend:", json.dumps(summary), flush=True)
    finally:
        subprocess.run(["docker", "exec", container, "node", "-e",
                        "require('node:fs').rmSync(process.argv[1], {recursive:true,force:true})",
                        directory], check=False)


if __name__ == "__main__":
    main()
