#!/usr/bin/env bash
set -euo pipefail
repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
environment="${1:?Usage: compose.sh local|current|original|oss <docker compose arguments>}"
shift
case "$environment" in
  local|current|original)
    project=hc-data-platform-dev
    config="$repo/.env"
    overlays=(-f "$repo/deploy/local/current.compose.yaml")
    ;;
  oss)
    project=hc-data-platform-oss
    config="$repo/.env"
    overlays=(-f "$repo/deploy/local/current.compose.yaml" -f "$repo/deploy/local/oss.compose.yaml")
    ;;
  *) echo 'Use local, current, original, or oss.' >&2; exit 2 ;;
esac
if [[ ! -f "$config" ]]; then
  echo "Missing deployment configuration: $config. See deploy/local/README.md." >&2
  exit 2
fi
# Preserve each deployment's explicit settings even when the invoking shell has
# HC_* variables for a different robot, database, or Compose project.
while IFS= read -r hc_variable; do unset "$hc_variable"; done < <(compgen -A variable HC_)
export HC_GIT_COMMIT="$(git -C "$repo" rev-parse HEAD)"
# Local capacity can observe this shared cache even before the OSS environment starts.
for compose_action in "$@"; do
  if [[ "$compose_action" == up || "$compose_action" == run || "$compose_action" == create ]]; then
    docker volume inspect hc-data-platform-oss_oss-runtime-cache >/dev/null 2>&1 ||
      docker volume create hc-data-platform-oss_oss-runtime-cache >/dev/null
    break
  fi
done
exec docker compose --project-directory "$repo" --env-file "$config" -p "$project" \
  -f "$repo/compose.dev.yaml" "${overlays[@]}" "$@"
