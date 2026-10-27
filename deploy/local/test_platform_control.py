"""Exercise the entry scripts with isolated service/HTTP commands, never live data."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[2]
FAKE_COMMAND = r'''#!/usr/bin/env python3
import json, os, pathlib, sys
name, args = pathlib.Path(sys.argv[0]).name, sys.argv[1:]
path = pathlib.Path(os.environ["PLATFORM_CONTROL_TEST_STATE"])
state = json.loads(path.read_text())
with path.with_suffix(".log").open("a") as stream:
    stream.write(json.dumps([name, *args]) + "\n")
def save():
    path.write_text(json.dumps(state))
if name == "sudo":
    os.execvp(args[0], args)
elif name == "git":
    print("a" * 40)
elif name == "systemctl":
    if state.get("bus_error") and "--user" not in args:
        sys.exit(1)
    unit = next(arg for arg in args if arg.endswith(".service"))
    if "show" in args:
        if "--property=LoadState" in args:
            loaded = (state.get("temporary", False) if "--user" in args
                      else not state.get("missing_tunnel", False))
            print("loaded" if loaded else "not-found")
        else:
            print("active" if state.get(unit, False) else "inactive")
    elif "is-active" in args:
        sys.exit(0 if state.get(unit, False) else 3)
    elif "start" in args or "stop" in args:
        if unit == "cloudflared.service" and "stop" in args and state.get("stop_tunnel_error"):
            sys.exit(1)
        state[unit] = "start" in args
        if "--user" in args:
            state["temporary"] = False
        save()
elif name == "docker":
    if args[0] == "info":
        sys.exit(0 if state.get("docker.service") and not state.get("docker_denied") else 1)
    elif "config" in args:
        print(json.dumps({"services": {
            "frontend-public": {"environment": {"VITE_ROBOT_PLATFORM_BASE_URL": state["origin"]}},
            "frontend": {"environment": {"__VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS": "158722.xyz"}},
            "gateway": {"ports": [{"target": 8080, "published": "18088"}]},
            "api": {"environment": {"PASSWORD": "must-not-be-printed"}},
        }}))
    elif "up" in args:
        if state.get("up_error"):
            sys.exit(1)
        state["containers_running"] = True
        save()
    elif "stop" in args:
        state["containers_running"] = False
        save()
    elif "logs" in args:
        print("migration-1 | migration failure detail")
    else:
        sys.exit("Unexpected Docker operation")
elif name == "curl":
    public = any(arg.startswith("https://") for arg in args)
    print("503" if public and state.get("public_error") else "200", end="")
elif name != "sleep":
    sys.exit("Unexpected command")
'''

FAKE_BUILD = '''import json, os
from pathlib import Path
path = Path(os.environ["PLATFORM_CONTROL_TEST_STATE"])
state = json.loads(path.read_text())
assert state["containers_running"], "The dev frontend must run before building"
state["builds"] = state.get("builds", 0) + 1
path.write_text(json.dumps(state))
destination = Path(__file__).resolve().parents[3] / ".data-platform-tools/cloudflare/frontend/index.html"
destination.parent.mkdir(parents=True, exist_ok=True)
destination.write_text("<html>bundled frontend</html>")
'''


class PlatformControlTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="hc-platform-control-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo = self.root / "platform"
        (self.repo / "deploy/local").mkdir(parents=True)
        for name in ("start.sh", "stop.sh", "deploy/local/platform.sh", "deploy/local/compose.sh"):
            shutil.copy2(REPO / name, self.repo / name)
        (self.repo / "deploy/local/build-public-frontend.py").write_text(FAKE_BUILD)
        self.env_text = "HC_PUBLIC_ORIGIN=https://158722.xyz\nSECRET=must-not-be-printed\n"
        (self.repo / ".env").write_text(self.env_text)
        self.bundle = self.root / ".data-platform-tools/cloudflare/frontend/index.html"
        self.bundle.parent.mkdir(parents=True)
        self.bundle.write_text("<html>existing frontend</html>")
        self.bin = self.root / "bin"
        self.bin.mkdir()
        for name in ("docker", "systemctl", "sudo", "curl", "git", "sleep"):
            command = self.bin / name
            command.write_text(FAKE_COMMAND)
            command.chmod(0o755)
        self.state_path = self.root / "state.json"
        self.state_path.write_text(json.dumps({
            "origin": "https://158722.xyz", "docker.service": False,
            "cloudflared.service": False, "containers_running": False,
        }))

    def state(self):
        return json.loads(self.state_path.read_text())

    def configure(self, **changes):
        state = self.state()
        state.update(changes)
        self.state_path.write_text(json.dumps(state))

    def calls(self):
        path = self.state_path.with_suffix(".log")
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def run_script(self, name, *args):
        result = subprocess.run(
            ["bash", str(self.repo / name), *args], cwd=self.root,
            env={**os.environ, "PATH": f"{self.bin}:{os.environ['PATH']}",
                 "PLATFORM_CONTROL_TEST_STATE": str(self.state_path)},
            capture_output=True, text=True, timeout=15,
        )
        self.assertNotIn("must-not-be-printed", result.stdout + result.stderr)
        self.assertEqual((self.repo / ".env").read_text(), self.env_text)
        return result

    def test_start_twice_reuses_bundle_and_checks_configured_port_and_domain(self):
        for _ in range(2):
            result = self.run_script("start.sh")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("启动成功：https://158722.xyz", result.stdout)
        self.assertTrue(self.state()["containers_running"])
        self.assertTrue(self.state()["cloudflared.service"])
        self.assertEqual(self.state().get("builds", 0), 0)
        requests = [call for call in self.calls() if call[0] == "curl"]
        self.assertTrue(any("http://127.0.0.1:18088/health/ready" in call for call in requests))
        self.assertTrue(any("Host: 158722.xyz" in call for call in requests))
        self.assertTrue(any("https://158722.xyz/health/ready" in call for call in requests))
        for call in requests:
            if any(arg.startswith("http://127.0.0.1:") for arg in call):
                self.assertIn("--noproxy", call)
            else:
                self.assertNotIn("--noproxy", call)

    def test_missing_bundle_builds_after_starting_dev_services(self):
        self.bundle.unlink()
        result = self.run_script("start.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.state()["builds"], 1)
        self.assertTrue(self.bundle.is_file())

    def test_explicit_build_refreshes_images_dependencies_and_public_bundle(self):
        result = self.run_script("start.sh", "--build")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.state()["builds"], 1)
        self.assertTrue(any("--build" in call and "-V" in call for call in self.calls()))

    def test_temporary_tunnel_stops_without_rewriting_environment(self):
        self.configure(temporary=True)
        result = self.run_script("start.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.state()["temporary"])

    def test_unreachable_domain_fails_but_preserves_started_services(self):
        self.configure(public_error=True)
        result = self.run_script("start.sh")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("启动成功", result.stdout)
        self.assertIn("本机平台已启动", result.stderr)
        self.assertTrue(self.state()["containers_running"])

    def test_invalid_origin_or_missing_tunnel_prevents_container_start(self):
        for changes in ({"origin": "https://old.trycloudflare.com"},
                        {"origin": "https://158722.xyz", "missing_tunnel": True}):
            self.configure(**changes)
            result = self.run_script("start.sh")
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(self.state()["containers_running"])

    def test_compose_failure_never_reports_success(self):
        self.configure(up_error=True)
        result = self.run_script("start.sh")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("启动成功", result.stdout)
        self.assertFalse(self.state()["cloudflared.service"])
        self.assertIn("migration failure detail", result.stderr)
        self.assertTrue(any("logs" in call and "--since" in call for call in self.calls()))

    def test_stop_twice_preserves_data_and_docker_daemon(self):
        self.configure(**{"docker.service": True, "cloudflared.service": True,
                          "containers_running": True, "temporary": True})
        for _ in range(2):
            result = self.run_script("stop.sh")
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.state()["containers_running"])
        self.assertFalse(self.state()["cloudflared.service"])
        self.assertTrue(self.state()["docker.service"])
        self.assertTrue(self.bundle.exists())
        for call in self.calls():
            self.assertFalse(set(call) & {"down", "rm", "prune", "-v"}, call)
        self.assertTrue(any("stop" in call and "cloudflare" in call and "tools" in call
                            for call in self.calls()))

    def test_stop_continues_after_tunnel_failure_and_reports_failure(self):
        self.configure(**{"docker.service": True, "cloudflared.service": True,
                          "containers_running": True, "stop_tunnel_error": True})
        result = self.run_script("stop.sh")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.state()["containers_running"])
        self.assertNotIn("平台和隧道已停止", result.stdout)

    def test_inaccessible_docker_or_systemd_cannot_report_stopped(self):
        for changes in ({"docker_denied": True}, {"docker_denied": False, "bus_error": True}):
            self.configure(**{"docker.service": True, **changes})
            result = self.run_script("stop.sh")
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("平台和隧道已停止", result.stdout)

    def test_help_and_invalid_options_do_not_change_services(self):
        self.assertEqual(self.run_script("start.sh", "--help").returncode, 0)
        self.assertEqual(self.run_script("stop.sh", "--build").returncode, 2)
        self.assertEqual(self.calls(), [])


if __name__ == "__main__":
    unittest.main()
