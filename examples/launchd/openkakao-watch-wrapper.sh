#!/bin/sh
set -eu

PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export PATH

LOG_DIR="${HOME}/Library/Logs/openkakao"
mkdir -p "${LOG_DIR}"

# Prefer the canonical name; retain the preexisting Homebrew compatibility path.
CLI=/opt/homebrew/bin/alden-cli
if [ ! -x "$CLI" ]; then CLI=/opt/homebrew/bin/openkakao-cli; fi
exec "$CLI" \
  --unattended \
  --allow-watch-side-effects \
  watch \
  --max-reconnect 20 \
  "$@"
