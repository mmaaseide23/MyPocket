#!/usr/bin/env bash
# Rebuild the static stylesheet from assets/tailwind.css.
# Usage:
#   ./scripts/build-css.sh           one-shot build
#   ./scripts/build-css.sh --watch   rebuild on every template change
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ ! -x bin/tailwindcss ]]; then
  echo "bin/tailwindcss missing — downloading…"
  mkdir -p bin
  curl -sSL -o bin/tailwindcss \
    https://github.com/tailwindlabs/tailwindcss/releases/latest/download/tailwindcss-macos-arm64
  chmod +x bin/tailwindcss
fi

bin/tailwindcss -i assets/tailwind.css -o mypocket/static/styles.css --minify "$@"
