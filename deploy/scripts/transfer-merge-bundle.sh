#!/usr/bin/env bash
# Retry transport failures without discarding an immutable exported merge bundle.
set -uo pipefail
umask 077

if [[ $# -ne 3 ]]; then
  printf '用法：bash %s 本机迁移包目录 用户@主机 远程目录\n' "$0" >&2
  printf '示例：bash %s artifacts/data-transfer-20260922 hc_op@192.168.31.126 workspace/data-transfer-20260922\n' "$0" >&2
  exit 2
fi

bundle=$(realpath -- "$1") || exit 2
remote=$2
remote_dir=${3%/}
if [[ ! -f "$bundle/manifest.json" || ! -f "$bundle/manifest.sha256" ]]; then
  printf '缺少完整的导出清单，请指定已导出的迁移包目录。\n' >&2
  exit 2
fi
if [[ ! "$remote" =~ ^[A-Za-z0-9_][A-Za-z0-9_.@-]*$ || -z "$remote_dir" || "$remote_dir" == -* ]]; then
  printf '远程地址或目录无效。\n' >&2
  exit 2
fi
for command in rsync ssh tee; do
  command -v "$command" >/dev/null || { printf '缺少命令：%s\n' "$command" >&2; exit 2; }
done

# 8 MiB/s is a conservative starting point for this unstable LAN connection.
# This may reduce load; it does not diagnose or repair the underlying connection.
bwlimit=${HC_TRANSFER_KIB_PER_SECOND:-8192}
attempts=${HC_TRANSFER_MAX_ATTEMPTS:-20}
if [[ ! "$bwlimit" =~ ^[0-9]+$ || ! "$attempts" =~ ^[1-9][0-9]*$ ]]; then
  printf '限速须为非负整数 KiB/s，重试次数须为正整数。\n' >&2
  exit 2
fi

log_dir="$(dirname -- "$bundle")/transfer-logs"
mkdir -p -- "$log_dir" || exit 2
log_file=$(mktemp "$log_dir/rsync-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX.log") || exit 2
printf '日志：%s\n每轮百分比独立计数；目标已有文件和未完成文件都会保留。\n' "$log_file"
printf 'SSH 重连时可能再次要求输入远程密码，密码不会写入日志。\n'
trap 'printf "\n传输已中止，已传文件保留，可再次运行本脚本。\n"; exit 130' INT TERM

ssh_transport='ssh -o ConnectTimeout=10 -o ServerAliveInterval=15 -o ServerAliveCountMax=6'
for ((attempt=1; attempt<=attempts; attempt++)); do
  printf '\n[%s] 第 %s/%s 轮，限速 %s KiB/s\n' "$(date -Is)" "$attempt" "$attempts" "$bwlimit" | tee -a "$log_file"
  rsync -a --protect-args --partial --append-verify --info=progress2 --stats \
    --timeout=60 --bwlimit="$bwlimit" -e "$ssh_transport" \
    -- "$bundle/" "$remote:$remote_dir/" 2>&1 | tee -a "$log_file"
  pipeline_status=("${PIPESTATUS[@]}")
  result=${pipeline_status[0]}
  if ((pipeline_status[1] != 0)); then
    printf '无法写入传输日志，请检查本机剩余空间。\n' >&2
    exit 1
  fi
  if ((result == 0)); then
    printf '\n传输完成。现在可以在远程执行 import，它会校验迁移包所有文件。\n' | tee -a "$log_file"
    exit 0
  fi
  case "$result" in
    10|12|30|35|255)
      if ((attempt < attempts)); then
        printf '\n连接中断（退出码 %s），5 秒后重试；已传文件保留。\n' "$result" | tee -a "$log_file"
        sleep 5
      fi
      ;;
    *)
      printf '\n传输失败（退出码 %s），停止自动重试；请检查上方错误。\n' "$result" | tee -a "$log_file"
      exit "$result"
      ;;
  esac
done
printf '\n已达到重试上限。已传文件保留，日志：%s\n' "$log_file" >&2
exit "$result"
