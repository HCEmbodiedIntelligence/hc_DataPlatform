#!/usr/bin/env bash
set -euo pipefail
repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
if [[ "$repo" == /home/hc_op/workspace/hc_DataPlatform ]]; then
  echo 'G1 requires an independent worktree.' >&2
  exit 2
fi
while IFS= read -r hc_variable; do unset "$hc_variable"; done < <(compgen -A variable HC_)
export HC_GIT_COMMIT="$(git -C "$repo" rev-parse HEAD)"
exec docker compose --project-directory "$repo" \
  --env-file "$repo/deploy/chayi/isolated.env" -p hc-chayi-g1 \
  -f "$repo/compose.dev.yaml" -f "$repo/deploy/chayi/images.compose.yaml" \
  -f "$repo/deploy/chayi/isolation.compose.yaml" "$@"
