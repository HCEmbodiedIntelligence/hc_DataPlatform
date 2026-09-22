#!/usr/bin/env python3
"""One-command local Compose export/merge, including pause, backup and service resume.

Uses the running API image and its mounted source, so Python changes take effect without
rebuilding images. Credentials travel through a private temporary env file, never argv.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
    return subprocess.run(command, check=True, **kwargs)


def inspect(container: str) -> dict[str, Any]:
    return json.loads(
        run(["docker", "inspect", container], capture_output=True).stdout
    )[0]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="开发环境数据合并：自动暂停写入、备份、校验和恢复服务"
    )
    parser.add_argument("command", choices=("export", "plan", "import"))
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, default=Path("artifacts/data-merge"))
    args = parser.parse_args()
    os.umask(0o077)
    root = Path.cwd()
    if (root / "run.sh").is_file() and (root / "compose.images.yaml").is_file():
        compose = [str(root / "run.sh")]
    elif (root / "compose.dev.yaml").is_file():
        compose = ["docker", "compose", "-f", str(root / "compose.dev.yaml")]
    else:
        parser.error("请在项目根目录或离线部署包根目录运行")
    bundle = args.bundle.expanduser().resolve()
    if args.command == "export":
        if bundle.exists():
            parser.error("导出目录已存在，请使用新的目录名")
        bundle.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    elif not (bundle / "manifest.json").is_file():
        parser.error("迁移包缺少 manifest.json")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    work = args.work_dir.expanduser().resolve() / stamp
    work.mkdir(parents=True, mode=0o700)
    services = {}
    for service in ("api", "worker", "frontend", "gateway", "object-store-browser"):
        cid = run(
            [*compose, "ps", "--all", "--quiet", service],
            capture_output=True,
            text=True,
        ).stdout.strip()
        if cid:
            services[service] = inspect(cid)
    if "api" not in services:
        parser.error("请先初始化 Compose 应用容器")
    api = services["api"]
    networks = list(api["NetworkSettings"]["Networks"])
    backend = next(
        (
            m["Source"]
            for m in api["Mounts"]
            if m["Destination"] == "/app" and m["Type"] == "bind"
        ),
        None,
    )
    if not backend or len(networks) != 1:
        parser.error("仅支持单网络、绑定挂载源码的 compose.dev 部署")
    if not (Path(backend) / "src/hc_data_platform/backup/merge_cli.py").is_file():
        parser.error("请先同步新增的迁移工具源码")
    environment = dict(item.split("=", 1) for item in api["Config"]["Env"])
    environment["HC_MERGE_QUIESCED"] = "true"
    if any("\n" in v or "\r" in v for v in environment.values()):
        parser.error("容器环境含多行值，无法安全转交给维护容器")
    running = [name for name, info in services.items() if info["State"]["Running"]]
    print(
        "将暂停应用写入，耗时取决于数据量；数据库、对象存储和 Temporal 保持运行。",
        flush=True,
    )
    resumed = False
    with tempfile.NamedTemporaryFile(
        mode="w", prefix="hc-merge-env-", delete=True
    ) as env:
        for key, value in environment.items():
            env.write(f"{key}={value}\n")
        env.flush()
        helper = [
            "docker",
            "run",
            "--rm",
            "--pull=never",
            "--no-healthcheck",
            "--label",
            "com.docker.compose.project=hc-data-merge-maintenance",
            "--label",
            "com.docker.compose.service=maintenance",
            "--label",
            "com.docker.compose.oneoff=True",
            "--network",
            networks[0],
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--env-file",
            env.name,
            "--mount",
            f"type=bind,source={backend},target=/app,readonly",
            "--mount",
            f"type=bind,source={work},target=/merge-work",
            "--entrypoint",
            "python",
        ]
        if args.command == "export":
            helper += [
                "--mount",
                f"type=bind,source={bundle.parent},target=/merge-bundles",
            ]
            container_bundle = "/merge-bundles/" + bundle.name
        else:
            helper += [
                "--mount",
                f"type=bind,source={bundle},target=/merge-bundle,readonly",
            ]
            container_bundle = "/merge-bundle"
        helper += [api["Image"], "-m", "hc_data_platform.backup.merge_cli"]
        try:
            if running:
                run([*compose, "stop", "--timeout", "90", *running])
            run([*helper, "check"])
            extra = []
            if args.command == "import":
                backup = work / "target-before.dump"
                with backup.open("xb") as stream:
                    run(
                        [
                            *compose,
                            "exec",
                            "-T",
                            "postgres",
                            "pg_dump",
                            "-U",
                            "hc",
                            "--format=custom",
                            "--dbname=hc_data",
                        ],
                        stdout=stream,
                    )
                with backup.open("rb") as stream:
                    run(
                        [*compose, "exec", "-T", "postgres", "pg_restore", "--list"],
                        stdin=stream,
                        stdout=subprocess.DEVNULL,
                    )
                extra = ["--rollback-dump", "/merge-work/target-before.dump"]
            run(
                [
                    *helper,
                    args.command,
                    "--bundle",
                    container_bundle,
                    "--report",
                    "/merge-work/report.json",
                    *extra,
                ]
            )
        finally:
            if running:
                run([*compose, "start", *running])
            resumed = True
    if resumed:
        print(f"原先运行的应用服务已恢复。结果：{work / 'report.json'}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        print(
            f"迁移未完成（退出码 {exc.returncode}），请查看上方错误；目标库未执行整库覆盖。",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
