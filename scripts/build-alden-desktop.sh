#!/bin/sh
# Build the release CLI and the Tauri Alden application bundle.
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
DESKTOP="$ROOT/desktop"
APP_NAME="Alden.app"
APP="$DESKTOP/src-tauri/target/release/bundle/macos/$APP_NAME"
APP_BIN="$APP/Contents/MacOS/openkakao-alden-desktop"

if [ ! -f "$DESKTOP/package.json" ] || [ ! -f "$DESKTOP/src-tauri/tauri.conf.json" ]; then
  echo "build-alden-desktop: desktop project is incomplete: $DESKTOP" >&2
  exit 2
fi

if ! (cd "$ROOT" && shasum -a 256 -c scripts/menubar-bytecode.sha256); then
  echo "build-alden-desktop: pinned menu runtime is missing or changed" >&2
  exit 2
fi

(
  cd "$ROOT"
  cargo build --release --bin openkakao-cli
)

if [ ! -x "$ROOT/target/release/openkakao-cli" ]; then
  echo "build-alden-desktop: release CLI was not produced" >&2
  exit 2
fi

(
  cd "$DESKTOP"
  npm run build

  if [ -n "${OPENKAKAO_SIGN_IDENTITY:-}" ]; then
    APPLE_SIGNING_IDENTITY=$OPENKAKAO_SIGN_IDENTITY
    export APPLE_SIGNING_IDENTITY
    npm run tauri -- build --bundles app
  else
    # Do not inherit an unrelated signing identity. Local builds are unsigned;
    # callers may explicitly request an ad-hoc identity with
    # OPENKAKAO_SIGN_IDENTITY=-.
    unset APPLE_SIGNING_IDENTITY
    npm run tauri -- build --bundles app --no-sign
  fi
)

if [ ! -x "$APP_BIN" ]; then
  echo "build-alden-desktop: Tauri app was not produced at $APP" >&2
  exit 2
fi

printf '%s\n' "$APP"
