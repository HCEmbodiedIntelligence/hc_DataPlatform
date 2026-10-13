#!/usr/bin/env bash
set -euo pipefail
repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
case "${1:-help}" in
  usage|list|status)
    exec bash "$repo/deploy/local/files.sh" "$@"
    ;;
  objects|trash|restore)
    exec bash "$repo/deploy/local/compose.sh" current exec -T api \
      python -m hc_data_platform.tools.local_storage_cli "$@"
    ;;
  *)
    echo 'Usage: data.sh usage [prefix] | list [prefix] | status | objects'
    echo '       data.sh trash OBJECT_ID --reason REASON [--organization ID --project ID]'
    echo '       data.sh restore OBJECT_ID --reason REASON [--organization ID --project ID]'
    exit 2
    ;;
esac
