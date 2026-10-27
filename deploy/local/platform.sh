#!/usr/bin/env bash
# Control this machine's existing Compose deployment and fixed-domain tunnel.
set -euo pipefail
repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"

usage() {
  printf '用法：\n  bash start.sh          启动平台和固定域名隧道\n  bash start.sh --build  重建应用镜像和外网页面后启动\n  bash stop.sh           停止平台和隧道，保留数据\n'
}

action="${1:-}"
rebuild=false
if [[ "${2:-}" == --help || "${2:-}" == -h ]]; then
  usage
  exit 0
fi
case "$action:$#:${2:-}" in
  start:1:|stop:1:) ;;
  start:2:--build) rebuild=true ;;
  *) usage >&2; exit 2 ;;
esac

fail() { printf '错误：%s\n' "$*" >&2; exit 1; }
compose() { bash "$repo/deploy/local/compose.sh" current "$@"; }
compose_up() {
  local started_at
  started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  if ! compose "$@"; then
    printf '\n平台启动失败，以下为本次启动的迁移日志：\n' >&2
    compose --profile cloudflare logs --no-color --since "$started_at" --tail=60 migration >&2 || true
    fail '平台尚未启动完成，请查看上方具体错误。'
  fi
}
system_service() {
  if (( EUID == 0 )); then
    systemctl "$@"
  else
    sudo systemctl "$@"
  fi
}

stop_temporary_tunnel() {
  local loaded
  loaded="$(systemctl --user show hc-platform-cloudflare.service --property=LoadState --value 2>/dev/null || true)"
  if [[ "$loaded" == loaded ]]; then
    printf '停止旧临时隧道…\n'
    # Do not call cloudflare.py stop: it restores the old origin and stops the public frontend.
    systemctl --user stop hc-platform-cloudflare.service
  fi
}

http_ok() {
  local status
  status="$(curl --silent --show-error --max-time 8 --output /dev/null \
    --write-out '%{http_code}' "$@" 2>/dev/null || true)"
  [[ "$status" == 200 ]]
}

wait_local() {
  local deadline=$((SECONDS + 120))
  until http_ok --noproxy '*' "$@"; do
    if (( SECONDS >= deadline )); then
      fail "本机服务尚未就绪。查看日志：bash deploy/local/compose.sh current --profile cloudflare logs --tail=100"
    fi
    sleep 2
  done
}

start_platform() {
  local command_name settings public_origin public_host gateway_port local_origin attempt reachable
  local -a values
  for command_name in docker systemctl python3 curl; do
    command -v "$command_name" >/dev/null || fail "缺少命令：$command_name"
  done
  [[ -f "$repo/.env" ]] || fail '缺少 .env，请先恢复本机部署配置。'
  [[ "$(systemctl show cloudflared.service --property=LoadState --value)" == loaded ]] \
    || fail '未安装固定域名隧道服务 cloudflared.service。'
  stop_temporary_tunnel

  # Read only the needed public values from Compose; never source .env as shell code
  # or print its database/storage credentials.
  settings="$(compose --profile cloudflare config --format json | python3 -c '
import json, sys
from urllib.parse import urlsplit
services = json.load(sys.stdin)["services"]
origin = services["frontend-public"]["environment"].get("VITE_ROBOT_PLATFORM_BASE_URL", "").rstrip("/")
url = urlsplit(origin)
if (url.scheme != "https" or not url.hostname or url.username or url.password
        or url.path or url.query or url.fragment
        or url.hostname == "trycloudflare.com" or url.hostname.endswith(".trycloudflare.com")):
    sys.exit("请将 .env 的 HC_PUBLIC_ORIGIN 设置为固定 HTTPS 域名，例如 https://158722.xyz。")
host = services["frontend"]["environment"].get("__VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS")
if host != url.hostname:
    sys.exit(".env 的 HC_TUNNEL_HOSTNAME 必须与 HC_PUBLIC_ORIGIN 中的域名一致。")
port = next(p["published"] for p in services["gateway"]["ports"] if p["target"] == 8080)
print(origin)
print(url.netloc)
print(port)
')"
  mapfile -t values <<< "$settings"
  public_origin="${values[0]}"
  public_host="${values[1]}"
  gateway_port="${values[2]}"
  local_origin="http://127.0.0.1:$gateway_port"

  if ! systemctl is-active --quiet docker.service; then
    printf '启动 Docker（可能需要输入 sudo 密码）…\n'
    system_service start docker.service
  fi
  docker info >/dev/null || fail '无法访问 Docker，请检查当前用户的 Docker 权限。'

  printf '启动平台…\n'
  if "$rebuild"; then
    compose_up up --build -V -d
  elif [[ ! -s "$repo/../.data-platform-tools/cloudflare/frontend/index.html" ]]; then
    compose_up up -d
  fi
  if "$rebuild" || [[ ! -s "$repo/../.data-platform-tools/cloudflare/frontend/index.html" ]]; then
    printf '构建外网页面…\n'
    python3 "$repo/deploy/local/build-public-frontend.py"
  fi
  compose_up --profile cloudflare up -d
  printf '等待 API 和外网页面就绪…\n'
  wait_local "$local_origin/health/ready"
  wait_local --header "Host: $public_host" "$local_origin/"

  if ! systemctl is-active --quiet cloudflared.service; then
    printf '启动固定域名隧道（可能需要输入 sudo 密码）…\n'
    system_service start cloudflared.service
  fi
  systemctl is-active --quiet cloudflared.service || fail '固定域名隧道未启动成功。'
  printf '检查固定域名 %s …\n' "$public_origin"
  reachable=false
  for attempt in 1 2 3; do
    if http_ok "$public_origin/health/ready" && http_ok "$public_origin/"; then
      reachable=true
      break
    fi
    if (( attempt < 3 )); then sleep 2; fi
  done
  if ! "$reachable"; then
    printf '本机平台已启动：%s\n' "$local_origin" >&2
    fail "固定域名尚未通过检查，服务已保留运行。请检查 Cloudflare 隧道和 DNS；日志：sudo journalctl -u cloudflared.service -n 80 --no-pager"
  fi
  printf '\n启动成功：%s\n本机入口：%s\n停止命令：bash %s/stop.sh\n' "$public_origin" "$local_origin" "$repo"
}

stop_platform() {
  local result=0 tunnel_state
  printf '停止隧道和平台，保留数据库与上传文件…\n'
  stop_temporary_tunnel || result=1
  tunnel_state="$(systemctl show cloudflared.service --property=ActiveState --value)" || result=1
  if [[ -n "$tunnel_state" && "$tunnel_state" != inactive && "$tunnel_state" != failed ]]; then
    system_service stop cloudflared.service || result=1
  fi
  if docker info >/dev/null 2>&1; then
    compose --profile cloudflare --profile tools stop --timeout 60 || result=1
  else
    printf '无法访问 Docker，容器停止状态未确认；请启动 Docker 并检查权限后重试。\n' >&2
    result=1
  fi
  (( result == 0 )) || fail '部分服务停止失败，请查看上方错误并重试。'
  printf '平台和隧道已停止，数据已保留。\n'
}

case "$action" in
  start) start_platform ;;
  stop) stop_platform ;;
esac
