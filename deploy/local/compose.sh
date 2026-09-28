#!/usr/bin/env bash
set -euo pipefail
repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
environment="${1:?Usage: compose.sh original|openarm <docker compose arguments>}"
shift
case "$environment" in
  original)
    project=hc-data-platform-restore-test
    config="$repo/deploy/local/original.env"
    overlays=(-f "$repo/deploy/local/original.compose.yaml")
    ;;
  openarm)
    project=openarm-e
    config="$repo/deploy/openarm-e/E.env"
    overlays=(-f "$repo/deploy/openarm-e/compose.json" -f "$repo/deploy/openarm-e/compose-override.yaml")
    ;;
  *) echo 'Expected original or openarm' >&2; exit 2 ;;
esac
if [[ ! -f "$config" ]]; then
  echo "Missing deployment configuration: $config. See deploy/local/README.md." >&2
  exit 2
fi
# Preserve each deployment's explicit settings even when the invoking shell has
# HC_* variables for a different robot, database, or Compose project.
while IFS= read -r hc_variable; do unset "$hc_variable"; done < <(compgen -A variable HC_)
export HC_GIT_COMMIT="$(git -C "$repo" rev-parse HEAD)"
exec docker compose --project-directory "$repo" --env-file "$config" -p "$project" \
  -f "$repo/compose.dev.yaml" "${overlays[@]}" "$@"
