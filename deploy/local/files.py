"""Browse the authoritative local MinIO bucket without a second data copy."""

from __future__ import annotations

import argparse
import configparser
import json
import os
import secrets
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import quote

REPO = Path(__file__).resolve().parents[2]
STATE = REPO.parent / ".data-platform-tools"
CONFIG = STATE / "rclone.conf"
BINARY = STATE / "rclone"
REMOTE = "local:hc-data-local"
UNIT = "hc-platform-files.service"


def output(args: list[str]) -> str:
    return subprocess.check_output(args, text=True)


def run(args: list[str], **kwargs) -> None:
    subprocess.run(args, check=True, **kwargs)


def settings() -> dict[str, str]:
    result = {}
    for line in (REPO / ".env").read_text().splitlines():
        if line.startswith("HC_") and "=" in line:
            key, value = line.split("=", 1)
            result[key] = value.strip().strip("\"'")
    return result


def directory() -> Path:
    return Path(
        settings().get(
            "HC_LOCAL_FILES_DIRECTORY", str(REPO.parent / "data-platform-files")
        )
    )


def rclone(*args: str) -> list[str]:
    if not BINARY.is_file() or not CONFIG.is_file():
        raise RuntimeError(
            "Run the documented rclone installation and files.sh setup first."
        )
    return [str(BINARY), "--config", str(CONFIG), *args]


def setup() -> None:
    """Create a bucket-scoped read-only account; never persist the MinIO root secret."""
    STATE.mkdir(mode=0o700, exist_ok=True)
    os.chmod(STATE, 0o700)
    target = directory()
    data = Path(
        settings().get(
            "HC_MINIO_DATA_DIRECTORY", str(REPO.parent / "data-platform-storage")
        )
    )
    if not target.is_absolute() or any(c in str(target) for c in "\n\r%"):
        raise ValueError(
            "Browse directory must be an absolute path without newlines or %."
        )
    for reserved in (data.resolve(), REPO.resolve(), STATE.resolve()):
        if target.resolve().is_relative_to(reserved) or reserved.is_relative_to(
            target.resolve()
        ):
            raise ValueError(
                "Browse directory must be separate from data, code and tool directories."
            )
    if os.path.ismount(target):
        raise RuntimeError("Stop the files mount before reconfiguring it.")
    if target.exists() and any(target.iterdir()):
        raise RuntimeError(
            "Browse directory must be empty; existing files are never hidden."
        )
    target.mkdir(mode=0o755, parents=True, exist_ok=True)
    container = output(
        ["bash", str(REPO / "deploy/local/compose.sh"), "current", "ps", "-q", "minio"]
    ).strip()
    inspection = json.loads(output(["docker", "inspect", container]))[0]
    environment = dict(
        item.split("=", 1) for item in inspection["Config"]["Env"] if "=" in item
    )
    port = inspection["NetworkSettings"]["Ports"]["9000/tcp"][0]["HostPort"]
    parser = configparser.ConfigParser(interpolation=None)
    if CONFIG.exists():
        parser.read(CONFIG)
    secret = parser.get("local", "secret_access_key", fallback=secrets.token_hex(32))
    user = "hc-local-browser"
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["s3:GetBucketLocation", "s3:ListBucket"],
                "Resource": ["arn:aws:s3:::hc-data-local"],
            },
            {
                "Effect": "Allow",
                "Action": ["s3:GetObject"],
                "Resource": ["arn:aws:s3:::hc-data-local/*"],
            },
        ],
    }
    connection = (
        "http://"
        + quote(environment["MINIO_ROOT_USER"], safe="")
        + ":"
        + quote(environment["MINIO_ROOT_PASSWORD"], safe="")
        + "@minio:9000"
    )
    with tempfile.TemporaryDirectory(prefix="hc-browser-setup-") as temporary:
        root = Path(temporary)
        env = root / "credentials"
        env.write_text(
            "MC_HOST_local=" + connection + "\nBROWSER_SECRET=" + secret + "\n"
        )
        env.chmod(0o600)
        policy_path = root / "policy.json"
        policy_path.write_text(json.dumps(policy))
        project = inspection["Config"]["Labels"]["com.docker.compose.project"]
        # Only fixed shell code; credentials are passed via an env-file, never interpolated.
        script = (
            "mc admin policy create local hc-local-browser /policy.json >/dev/null && "
            'mc admin user add local hc-local-browser "$BROWSER_SECRET" >/dev/null && '
            "mc admin policy attach local hc-local-browser --user hc-local-browser >/dev/null"
        )
        run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                project + "_default",
                "--env-file",
                str(env),
                "--mount",
                f"type=bind,source={policy_path},target=/policy.json,readonly",
                "--entrypoint",
                "/bin/sh",
                "hc-offline/mc:71eaa560",
                "-ec",
                script,
            ]
        )
    parser["local"] = {
        "type": "s3",
        "provider": "Minio",
        "env_auth": "false",
        "access_key_id": user,
        "secret_access_key": secret,
        "endpoint": f"http://127.0.0.1:{port}",
        "region": "us-east-1",
        "force_path_style": "true",
    }
    with CONFIG.open("w") as stream:
        parser.write(stream)
    CONFIG.chmod(0o600)
    print(f"Read-only file browser configured: {target}")


def mount() -> None:
    target = directory()
    if os.path.ismount(target) or any(target.iterdir()):
        raise RuntimeError("Browse directory is already mounted or is not empty.")
    command = rclone(
        "mount",
        REMOTE,
        str(target),
        "--read-only",
        "--vfs-cache-mode",
        "off",
        "--buffer-size",
        "8M",
        "--dir-cache-time",
        "5s",
        "--attr-timeout",
        "1s",
        "--file-perms",
        "0444",
        "--dir-perms",
        "0555",
        "--umask",
        "022",
        "--log-level",
        "NOTICE",
        "--volname",
        "平台数据",
    )
    os.execv(command[0], command)


def install_service() -> None:
    # The unit contains paths only. All credentials remain in CONFIG (mode 0600).
    unit_dir = Path.home() / ".config/systemd/user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    mount_script = REPO / "deploy/local/files.sh"
    unit = f'''[Unit]
Description=HC platform local data directory (read-only MinIO view)
After=network.target

[Service]
Type=simple
ExecStart=/bin/bash "{mount_script}" mount
ExecStop=/usr/bin/fusermount3 -u "{directory()}"
Restart=on-failure
RestartSec=5
TimeoutStopSec=20

[Install]
WantedBy=default.target
'''
    (unit_dir / UNIT).write_text(unit)
    run(["systemctl", "--user", "daemon-reload"])
    run(["systemctl", "--user", "enable", "--now", UNIT])


def human_size(value: int) -> str:
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.2f} {unit}"
        size /= 1024
    raise AssertionError("unreachable")


def usage(prefix: str) -> None:
    if prefix.startswith("/") or any(part in {".", ".."} for part in prefix.split("/")):
        raise ValueError("Use a relative object prefix.")
    result = json.loads(output(rclone("size", REMOTE + "/" + prefix, "--json")))
    print(f"目录：{directory() / prefix}")
    print(f"文件数量：{result['count']}")
    print(f"文件总大小：{human_size(int(result['bytes']))}（{result['bytes']} 字节）")
    print("以上为完整对象的内容大小，不含未完成分片、MinIO 元数据及数据库。")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=["setup", "install-service", "mount", "usage", "list", "status"],
    )
    parser.add_argument("prefix", nargs="?", default="")
    args = parser.parse_args()
    if args.action == "setup":
        setup()
    elif args.action == "install-service":
        install_service()
    elif args.action == "mount":
        mount()
    elif args.action == "usage":
        usage(args.prefix)
    elif args.action == "list":
        run(rclone("ls", REMOTE + "/" + args.prefix))
    else:
        print(
            json.dumps(
                {
                    "directory": str(directory()),
                    "mounted": os.path.ismount(directory()),
                    "read_only": True,
                    "extra_full_copy": False,
                },
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
