#!/usr/bin/env python3
"""Manage a temporary Cloudflare URL for the current local deployment."""

import argparse
import json
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, build_opener

REPO = Path(__file__).resolve().parents[2]
TOOLS = REPO.parent / ".data-platform-tools"
STATE_DIR = TOOLS / "cloudflare"
STATE_FILE = STATE_DIR / "state.json"
LOG_FILE = STATE_DIR / "tunnel.log"
ENV_FILE = REPO / ".env"
UNIT = "hc-platform-cloudflare"
KEYS = ("HC_PUBLIC_ORIGIN", "HC_TUNNEL_HOSTNAME")


def atomic_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
    try:
        with os.fdopen(fd, "w") as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(text)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_environment():
    values = {}
    for line in ENV_FILE.read_text().splitlines():
        key, separator, value = line.partition("=")
        if separator:
            values[key.strip()] = value
    return values


def update_environment(values):
    lines = [
        line for line in ENV_FILE.read_text().splitlines()
        if line.partition("=")[0].strip() not in values
    ]
    lines.extend(f"{key}={value}" for key, value in values.items() if value is not None)
    atomic_write(ENV_FILE, "\n".join(lines) + "\n")


def apply_environment():
    services = ["api", "worker", "frontend", "gateway"]
    if (STATE_DIR / "frontend/index.html").is_file():
        services.append("frontend-public")
    subprocess.run(
        ["bash", str(REPO / "deploy/local/compose.sh"), "current", "--profile", "cloudflare",
         "up", "-d", "--no-deps", "--no-build", *services],
        check=True, timeout=180,
    )


def build_frontend():
    subprocess.run([sys.executable, str(REPO / "deploy/local/build-public-frontend.py")],
                   check=True, timeout=360)


def load_state():
    return json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}


def save_state(state):
    atomic_write(STATE_FILE, json.dumps(state, ensure_ascii=False, indent=2) + "\n")


def activate(url):
    if not (STATE_DIR / "frontend/index.html").is_file():
        build_frontend()
    state = load_state()
    current = read_environment()
    if "original" not in state:
        state["original"] = {key: current.get(key) for key in KEYS}
    state.update(url=url, applied=False)
    save_state(state)
    update_environment({"HC_PUBLIC_ORIGIN": url, "HC_TUNNEL_HOSTNAME": urlsplit(url).hostname})
    apply_environment()
    state["applied"] = True
    save_state(state)
    print(f"Platform public URL: {url}", flush=True)


def restore():
    state = load_state()
    if "original" not in state:
        return
    current = read_environment()
    owned = {"HC_PUBLIC_ORIGIN": state.get("url"),
             "HC_TUNNEL_HOSTNAME": urlsplit(state.get("url", "")).hostname}
    # Preserve any settings the operator changed while the tunnel was running.
    values = {key: value for key, value in state["original"].items()
              if current.get(key) == owned[key]}
    if values:
        update_environment(values)
    # Also retry a previous restoration whose container recreation failed.
    apply_environment()
    save_state({"last_url": state.get("url"), "stopped": True})
    print("Tunnel stopped; previous platform address restored.")


def run_tunnel():
    STATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    binary = TOOLS / "cloudflared"
    if not binary.is_file():
        raise RuntimeError(f"Install the official cloudflared binary at {binary}")
    env = {key: value for key, value in os.environ.items()
           if key.lower() not in {"http_proxy", "https_proxy", "all_proxy"}}
    port = read_environment().get("HC_GATEWAY_HOST_PORT", "8088").strip("\"'")
    child = subprocess.Popen(
        [str(binary), "--no-autoupdate", "tunnel", "--protocol", "http2",
         "--edge-ip-version", "4", "--url", f"http://127.0.0.1:{port}",
         "--metrics", "127.0.0.1:20241"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
    )
    stopping = False

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True
        child.terminate()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    selector = selectors.DefaultSelector()
    selector.register(child.stdout, selectors.EVENT_READ)
    started = time.monotonic()
    pending = b""
    url = None
    connected = False
    applied = False
    try:
        with LOG_FILE.open("a", buffering=1) as log:
            while child.poll() is None:
                if not applied and not stopping and time.monotonic() - started > 120:
                    raise RuntimeError("Cloudflare did not connect within 120 seconds; see tunnel.log")
                for key, _ in selector.select(timeout=1):
                    data = os.read(key.fd, 65536)
                    pending += data
                    while b"\n" in pending:
                        raw, pending = pending.split(b"\n", 1)
                        line = raw.decode(errors="replace")
                        log.write(line + "\n")
                        print(line, flush=True)
                        match = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
                        if match:
                            url = match.group(0)
                        if "Registered tunnel connection" in line:
                            connected = True
                    if url and connected and not applied and not stopping:
                        activate(url)
                        applied = True
            if not stopping:
                raise RuntimeError(f"cloudflared exited with code {child.returncode}")
    finally:
        selector.close()
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "stop", "status", "logs", "build", "run"))
    args = parser.parse_args()
    if args.action == "run":
        run_tunnel()
    elif args.action == "build":
        build_frontend()
        subprocess.run(
            ["bash", str(REPO / "deploy/local/compose.sh"), "current", "--profile", "cloudflare",
             "up", "-d", "--no-deps", "--no-build", "frontend-public", "gateway"],
            check=True, timeout=120,
        )
    elif args.action == "start":
        if not (TOOLS / "cloudflared").is_file():
            parser.error(f"Missing {TOOLS / 'cloudflared'}")
        active = subprocess.run(["systemctl", "--user", "is-active", "--quiet", UNIT])
        if active.returncode == 0:
            print("Tunnel service is already running; use status for its URL.")
            return
        subprocess.run(
            ["systemd-run", "--user", "--collect", "--unit", UNIT,
             "--property=Restart=on-failure", "--property=RestartSec=15",
             "--property=TimeoutStopSec=60", "/usr/bin/python3", "-u",
             str(Path(__file__).resolve()), "run"], check=True,
        )
        print("Starting in the background. Use status/logs to inspect the URL.")
    elif args.action == "stop":
        loaded = subprocess.run(
            ["systemctl", "--user", "show", UNIT, "--property=LoadState", "--value"],
            check=True, capture_output=True, text=True,
        )
        if loaded.stdout.strip() != "not-found":
            subprocess.run(["systemctl", "--user", "stop", UNIT], check=True)
        restore()
        subprocess.run(
            ["bash", str(REPO / "deploy/local/compose.sh"), "current", "--profile", "cloudflare",
             "stop", "frontend-public"], check=True,
        )
    elif args.action == "status":
        active = subprocess.run(["systemctl", "--user", "is-active", UNIT], check=False)
        state = load_state()
        print(json.dumps(state, ensure_ascii=False, indent=2))
        if active.returncode == 0:
            try:
                with build_opener(ProxyHandler({})).open("http://127.0.0.1:20241/ready", timeout=5) as response:
                    print(f"Cloudflare connection readiness: HTTP {response.status}")
            except OSError as error:
                print(f"Cloudflare connection is not ready: {error}")
    else:
        subprocess.run(["journalctl", "--user", "-u", UNIT, "-n", "60", "--no-pager"], check=True)


if __name__ == "__main__":
    main()
