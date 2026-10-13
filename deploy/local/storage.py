"""Select a host MinIO directory or export ordinary files, without deleting data."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def copy_tree(source: Path, target: Path) -> None:
    for directory, dirs, files in os.walk(source):
        relative = Path(directory).relative_to(source)
        destination = target / relative
        destination.mkdir(parents=True, exist_ok=True)
        for name in dirs:
            if (Path(directory) / name).is_symlink():
                raise RuntimeError("MinIO data contains an unexpected symbolic link")
        for name in files:
            original = Path(directory) / name
            if original.is_symlink():
                raise RuntimeError("MinIO data contains an unexpected symbolic link")
            copied = destination / name
            shutil.copy2(original, copied)
            with original.open("rb") as left, copied.open("rb") as right:
                if (
                    hashlib.file_digest(left, "sha256").digest()
                    != hashlib.file_digest(right, "sha256").digest()
                ):
                    raise RuntimeError("data copy verification failed")
    print("MinIO files copied and SHA-256 verified.")


def main() -> None:
    if len(sys.argv) == 4 and sys.argv[1] == "__copy":
        copy_tree(Path(sys.argv[2]), Path(sys.argv[3]))
        return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["status", "set", "export-files"])
    parser.add_argument("directory", nargs="?")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    compose = ["bash", str(repo / "deploy/local/compose.sh"), "current"]

    def run(arguments, **kwargs):
        return subprocess.run(arguments, check=True, **kwargs)

    def output(arguments):
        return subprocess.check_output(arguments, text=True)

    container_id = output([*compose, "ps", "-aq", "minio"]).strip()
    if not container_id:
        raise RuntimeError(
            "The local MinIO deployment must exist before managing its directory."
        )
    inspection = json.loads(output(["docker", "inspect", container_id]))[0]
    mount = next(
        item for item in inspection["Mounts"] if item["Destination"] == "/data"
    )
    current = Path(mount["Source"])
    if args.action == "status":
        print(
            json.dumps(
                {
                    "provider": "local-minio",
                    "directory": str(current),
                    "mount_type": mount["Type"],
                    "bucket": "hc-data-local",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if not args.directory:
        parser.error("directory is required")
    requested = Path(args.directory).expanduser()
    if not requested.is_absolute() or any(
        c in str(requested) for c in "\n\r\x00$,#\"'\\"
    ):
        raise ValueError(
            "Use an absolute directory without shell/environment expansions."
        )
    target = requested.resolve()
    if target == current:
        if args.action == "set":
            print("Already using the requested directory.")
            return
        raise ValueError("Export must use a separate directory.")
    if (
        target == Path("/")
        or target.is_relative_to(current)
        or current.is_relative_to(target)
    ):
        raise ValueError(
            "Source and destination directories must not contain each other."
        )
    if target.is_relative_to(repo) or repo.is_relative_to(target):
        raise ValueError("Storage must be separate from the source repository.")
    if target.exists() and any(target.iterdir()):
        raise ValueError(
            "Destination must be empty; no existing files will be overwritten."
        )
    target.mkdir(parents=True, exist_ok=True)
    if args.action == "export-files":
        credentials = dict(
            item.split("=", 1) for item in inspection["Config"]["Env"] if "=" in item
        )
        from urllib.parse import quote

        connection = (
            "http://"
            + quote(credentials["MINIO_ROOT_USER"], safe="")
            + ":"
            + quote(credentials["MINIO_ROOT_PASSWORD"], safe="")
            + "@minio:9000"
        )
        with tempfile.NamedTemporaryFile(
            mode="w", prefix="hc-minio-export-", delete=False
        ) as secret:
            secret.write("MC_HOST_local=" + connection + "\nMC_CONFIG_DIR=/tmp/mc\n")
            secret_path = Path(secret.name)
        try:
            os.chmod(secret_path, 0o600)
            project = inspection["Config"]["Labels"]["com.docker.compose.project"]
            run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--network",
                    project + "_default",
                    "--user",
                    f"{os.getuid()}:{os.getgid()}",
                    "--env-file",
                    str(secret_path),
                    "--mount",
                    f"type=bind,source={target},target=/export",
                    "--entrypoint",
                    "mc",
                    "hc-offline/mc:71eaa560",
                    "mirror",
                    "local/hc-data-local",
                    "/export",
                ]
            )
        finally:
            secret_path.unlink(missing_ok=True)
        print(
            f"Ordinary files exported to {target}; database metadata is not included."
        )
        return
    env_file = repo / ".env"
    original_env = env_file.read_text()

    def configure(directory):
        lines = [
            line
            for line in original_env.splitlines()
            if not line.startswith("HC_MINIO_DATA_DIRECTORY=")
        ]
        lines.append("HC_MINIO_DATA_DIRECTORY=" + str(directory))
        env_file.write_text("\n".join(lines) + "\n")

    # Quiesce writers and MinIO before copying its internal data. PostgreSQL and
    # all account/project records remain untouched.
    print(f"Moving local MinIO storage to {target}.")
    run([*compose, "stop", "api", "worker", "minio"])
    try:
        source_mount = (
            f"type=volume,source={mount['Name']},target=/source,readonly"
            if mount["Type"] == "volume"
            else f"type=bind,source={current},target=/source,readonly"
        )
        run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--user",
                "0:0",
                "--mount",
                source_mount,
                "--mount",
                f"type=bind,source={target},target=/destination",
                "--mount",
                f"type=bind,source={Path(__file__).resolve()},target=/migration.py,readonly",
                "--entrypoint",
                "python",
                "hc-data-platform-backend:dev",
                "/migration.py",
                "__copy",
                "/source",
                "/destination",
            ]
        )
        configure(target)
        run(
            [
                *compose,
                "up",
                "-d",
                "--no-build",
                "--pull",
                "never",
                "--wait",
                "minio",
                "api",
                "worker",
            ]
        )
    except BaseException:
        # A named volume is also accessible through its exact host mountpoint;
        # the bind-mount overlay can safely restore that original byte store.
        configure(current)
        run(
            [
                *compose,
                "up",
                "-d",
                "--no-build",
                "--pull",
                "never",
                "--wait",
                "minio",
                "api",
                "worker",
            ]
        )
        raise
    print(
        "Storage directory selected. Existing accounts, data and task references are preserved."
    )


if __name__ == "__main__":
    main()
