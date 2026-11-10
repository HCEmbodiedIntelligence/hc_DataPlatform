#!/usr/bin/env bash
set -euo pipefail
root="$(cd -- "$(dirname -- "$0")" && pwd)"
envfile="${HC_MOVE_ENV_FILE:-$HOME/.config/hc-data-platform/oss.env}"
[[ -r "$envfile" ]] || { echo "Missing existing deployment settings: $envfile" >&2; exit 2; }
# Follow the source launcher's precedence, including after an operator changed shell profiles.
while IFS= read -r name; do unset "$name"; done < <(compgen -A variable HC_)
exec docker compose --project-directory "$root" -p hc-data-platform-oss --env-file "$envfile" \
  -f "$root/compose.dev.yaml" -f "$root/deploy/local/current.compose.yaml" \
  -f "$root/deploy/local/oss.compose.yaml" -f "$root/new-host.yaml" -f "$root/images.json" "$@"
