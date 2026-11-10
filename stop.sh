#!/usr/bin/env bash
set -euo pipefail
repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
case "${1:-}" in
  oss) shift; exec bash "$repo/deploy/local/platform-oss.sh" stop "$@" ;;
  local) shift ;;
esac
exec bash "$repo/deploy/local/platform.sh" stop "$@"
