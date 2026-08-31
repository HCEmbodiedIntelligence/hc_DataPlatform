#!/bin/sh
set -eu

api_base_url="${VITE_API_BASE_URL:-/api/v1}"
sse_base_url="${VITE_SSE_BASE_URL:-/api/v1/events}"
mock_mode="${VITE_MOCK_MODE:-off}"
build_version="${VITE_BUILD_VERSION:-}"
release_env="${VITE_RELEASE_ENV:-}"
platform_version="${VITE_PLATFORM_VERSION:-}"
git_commit="${VITE_GIT_COMMIT:-}"
chart_version="${VITE_CHART_VERSION:-}"
release_manifest_digest="${VITE_RELEASE_MANIFEST_DIGEST:-}"
migration_manifest_digest="${VITE_MIGRATION_MANIFEST_DIGEST:-}"
component_image_digest="${VITE_COMPONENT_IMAGE_DIGEST:-}"
runtime_dir=/tmp/hc-runtime

validate_public_url() {
  value="$1"
  name="$2"
  if ! printf '%s' "$value" | grep -Eq '^(/|https://)[A-Za-z0-9._~:/?#@!$&()*+,;=%-]+$'; then
    echo "$name must be an application-root path or an HTTPS URL" >&2
    exit 1
  fi
}

validate_public_url "$api_base_url" VITE_API_BASE_URL
validate_public_url "$sse_base_url" VITE_SSE_BASE_URL

if [ "$mock_mode" != off ]; then
  echo "VITE_MOCK_MODE must be off in a container release" >&2
  exit 1
fi

case "$release_env" in
  dev|test|staging|production) ;;
  *) echo "VITE_RELEASE_ENV must be dev, test, staging, or production" >&2; exit 1 ;;
esac

if ! printf '%s' "$build_version" | grep -Eq '^[A-Za-z0-9._-]{1,128}$'; then
  echo "VITE_BUILD_VERSION must be a non-empty release identifier" >&2
  exit 1
fi

if ! printf '%s' "$platform_version" | grep -Eq '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$'; then
  echo "VITE_PLATFORM_VERSION must be a three-component semantic version" >&2
  exit 1
fi
if ! printf '%s' "$chart_version" | grep -Eq '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$'; then
  echo "VITE_CHART_VERSION must be a three-component semantic version" >&2
  exit 1
fi

validate_release_digest() {
  value="$1"
  name="$2"
  if [ "$value" != unreleased ] && ! printf '%s' "$value" | grep -Eq '^sha256:[0-9a-f]{64}$'; then
    echo "$name must be unreleased or a lowercase sha256 digest" >&2
    exit 1
  fi
}

validate_release_digest "$release_manifest_digest" VITE_RELEASE_MANIFEST_DIGEST
validate_release_digest "$migration_manifest_digest" VITE_MIGRATION_MANIFEST_DIGEST
validate_release_digest "$component_image_digest" VITE_COMPONENT_IMAGE_DIGEST

case "$release_env" in
  staging|production)
    if ! printf '%s' "$build_version" | grep -Eq '^platform-v[A-Za-z0-9][A-Za-z0-9._-]{0,119}$'; then
      echo "VITE_BUILD_VERSION must be an immutable platform-v release outside dev/test" >&2
      exit 1
    fi
    if ! printf '%s' "$git_commit" | grep -Eq '^[0-9a-f]{40}$'; then
      echo "VITE_GIT_COMMIT must be a full lowercase Git SHA-1 outside dev/test" >&2
      exit 1
    fi
    for digest in "$release_manifest_digest" "$migration_manifest_digest" "$component_image_digest"; do
      if [ "$digest" = unreleased ] || printf '%s' "$digest" | grep -Eq '^sha256:0{64}$'; then
        echo "release digests must be nonzero and immutable outside dev/test" >&2
        exit 1
      fi
    done
    ;;
  *)
    if [ "$git_commit" != unknown ] && ! printf '%s' "$git_commit" | grep -Eq '^[0-9a-f]{40}$'; then
      echo "VITE_GIT_COMMIT must be unknown or a full lowercase Git SHA-1" >&2
      exit 1
    fi
    ;;
esac

mkdir -p "$runtime_dir"
umask 077
runtime_file="$runtime_dir/config.js"
temporary_file="$runtime_file.tmp"
printf '%s\n' \
  'globalThis.__HC_RUNTIME_CONFIG__ = Object.freeze({' \
  "  VITE_API_BASE_URL: \"$api_base_url\"," \
  "  VITE_SSE_BASE_URL: \"$sse_base_url\"," \
  '  VITE_MOCK_MODE: "off",' \
  "  VITE_BUILD_VERSION: \"$build_version\"," \
  "  VITE_RELEASE_ENV: \"$release_env\"," \
  "  VITE_PLATFORM_VERSION: \"$platform_version\"," \
  "  VITE_GIT_COMMIT: \"$git_commit\"," \
  "  VITE_CHART_VERSION: \"$chart_version\"," \
  "  VITE_RELEASE_MANIFEST_DIGEST: \"$release_manifest_digest\"," \
  "  VITE_MIGRATION_MANIFEST_DIGEST: \"$migration_manifest_digest\"," \
  "  VITE_COMPONENT_IMAGE_DIGEST: \"$component_image_digest\"" \
  '});' >"$temporary_file"
mv "$temporary_file" "$runtime_file"

logging_file="$runtime_dir/logging.conf"
logging_temporary_file="$logging_file.tmp"
printf '%s\n' "log_format hc_json escape=json '{\"schema_version\":\"hc-runtime-log/v1\",\"timestamp\":\"\$time_iso8601\",\"severity\":\"INFO\",\"service\":\"hc-data-platform-frontend\",\"instance_id\":\"\$hostname:\$pid\",\"node_name\":\"\$hostname\",\"role\":\"frontend\",\"release_id\":\"$build_version\",\"request_id\":\"\$request_id\",\"trace_id\":null,\"operation_id\":null,\"workflow_id\":null,\"event_code\":\"HTTP.REQUEST_COMPLETED\",\"duration_ms\":null,\"retry_count\":null,\"error_type\":null,\"route\":\"/\",\"http_method\":\"\$request_method\",\"status_code\":\$status}';" >"$logging_temporary_file"
mv "$logging_temporary_file" "$logging_file"

exec "$@"
