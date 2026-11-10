#!/usr/bin/env bash
set -euo pipefail
umask 077
cd "$(dirname "$0")"
sha256sum -c SHA256SUMS
root=/mnt/workspace/hc-platform
[[ $EUID == 0 && -s "$root/config/oss.env" && -s "$root/db-backups/dataset-heads.restored.txt" ]]
if ! command -v supervisord >/dev/null || [[ ! -x /usr/sbin/nginx ]] || ! command -v setfacl >/dev/null || ! command -v ffmpeg >/dev/null || ! command -v rsync >/dev/null; then
 (umask 022; export DEBIAN_FRONTEND=noninteractive; apt-get -o Acquire::Retries=3 update && apt-get install -y --no-install-recommends supervisor nginx-light acl ffmpeg rsync libusb-1.0-0 libgl1 libgomp1)
fi
id=$(sha256sum runtime.tar.gz | cut -d' ' -f1)
if [[ ! -e "$root/runtime" ]]; then
  st=$(mktemp -d "$root/.runtime-XXXXXX")
  tar -xzf runtime.tar.gz -C "$st" --no-same-owner
  printf '%s\n' "$id" > "$st/bundle-id"
  mv -T "$st" "$root/runtime"
fi
[[ "$(cat "$root/runtime/bundle-id")" == "$id" ]] || { echo 'A different runtime already exists.' >&2; exit 1; }
getent passwd hcplatform >/dev/null || useradd --system --no-create-home --shell /usr/sbin/nologin hcplatform
python3 -c 'import sys; assert sys.version_info[:2]==(3,12), "Expected Python 3.12"'
python3 -m venv --without-pip "$root/runtime/venv"
PYTHONPATH="$root/app/backend/src:$root/runtime/python" "$root/runtime/venv/bin/python" -c 'import av,duckdb,lance,mcap_ros2.decoder,pyarrow,pyrealsense2,temporalio,oss2,psycopg; import hc_data_platform.core.app'
install -d -m 700 "$root/service"
install -d -m 700 -o hcplatform -g hcplatform "$root/service/nginx" "$root/cache/service-home" "$root/cache/robot-assets"
setfacl -m u:hcplatform:--x "$root" "$root/service"
setfacl -R -m u:hcplatform:rX "$root/runtime" "$root/app/backend" "$root/assets"
setfacl -m u:hcplatform:--x "$root/app" "$root/cache"
chown -R hcplatform:hcplatform "$root/cache/"*
chmod 600 "$root/config/oss.env"
bash "$root/runtime/ctl/manage.sh" "${1:-resume}"
