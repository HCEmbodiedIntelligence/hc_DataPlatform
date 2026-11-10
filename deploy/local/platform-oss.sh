#!/usr/bin/env bash
set -euo pipefail
repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
action="${1:-}"
shift || true
compose() { bash "$repo/deploy/local/compose.sh" oss "$@"; }
case "$action" in
  start)
    if [[ $# -gt 1 || ($# -eq 1 && "$1" != --build) ]]; then
      echo 'Usage: bash start.sh oss [--build]' >&2; exit 2
    fi
    options=()
    [[ ${1:-} != --build ]] || options+=(--build)
    compose up -d "${options[@]}" postgres temporal migration runtime-cache-init api frontend gateway worker
    echo 'OSS instance: http://127.0.0.1:8089 (separate database and caches).'
    echo 'The existing local instance and its tunnel are unchanged.'
    ;;
  stop)
    [[ $# -eq 0 ]] || { echo 'Usage: bash stop.sh oss' >&2; exit 2; }
    compose stop --timeout 330
    echo 'OSS instance stopped; its data and the local instance are preserved.'
    ;;
  *) echo 'Usage: platform-oss.sh start|stop [--build]' >&2; exit 2 ;;
esac
