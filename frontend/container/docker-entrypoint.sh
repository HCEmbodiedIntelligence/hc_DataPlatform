#!/bin/sh
set -eu

api_base_url="${VITE_API_BASE_URL:-/api/v1}"
sse_base_url="${VITE_SSE_BASE_URL:-/api/v1/events}"
mock_mode="${VITE_MOCK_MODE:-off}"
build_version="${VITE_BUILD_VERSION:-}"
release_env="${VITE_RELEASE_ENV:-}"
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
  "  VITE_RELEASE_ENV: \"$release_env\"" \
  '});' >"$temporary_file"
mv "$temporary_file" "$runtime_file"

exec "$@"

