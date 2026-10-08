#!/bin/sh
# Build the release CLI and the Tauri Alden application bundle.
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
if [ "${ALDEN_CANONICAL_BUILD_LOCKED:-0}" != "1" ]; then
  exec python3 "$ROOT/scripts/alden_build_receipt.py" run --root "$ROOT"
fi
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

# A receipt is written only after one complete canonical build. Reuse compares
# source, locks, resources, toolchain, CLI, every bundle file and strict signature.
# Evaluation requests deliberately run the ordinary build path.
if [ "${OPENKAKAO_FORCE_BUILD:-0}" != "1" ] && [ -z "${ALDEN_EVALUATION_DATASET:-}" ]; then
  if python3 "$ROOT/scripts/alden_build_receipt.py" check --root "$ROOT"; then
    exit 0
  fi
fi
ALDEN_BUILD_INPUT_KEY=$(python3 "$ROOT/scripts/alden_build_receipt.py" key --root "$ROOT")

# Explicit local evaluation settings make each changed source/model/dataset
# version run once. No evaluator, download or training starts by default.
if [ -n "${ALDEN_EVALUATION_DATASET:-}" ]; then
  : "${ALDEN_EVALUATION_PYTHON:?pinned evaluation Python is required}"
  : "${ALDEN_EVALUATION_POLICY_DIR:?local policy checkpoint is required}"
  : "${ALDEN_EVALUATION_REFERENCE_DIR:?local reference checkpoint is required}"
  : "${ALDEN_EVALUATION_ROOT:?private evaluation root is required}"
  : "${ALDEN_EVALUATION_CHECKPOINT_FORMAT:?explicit checkpoint format is required}"
  EVALUATION_VERSION=$("$ALDEN_EVALUATION_PYTHON" -c \
    'import json,sys; print(json.load(open(sys.argv[1]))["version"])' \
    "$DESKTOP/src-tauri/tauri.conf.json")
  set -- "$ALDEN_EVALUATION_PYTHON" "$ROOT/scripts/auto_reply_finetune.py" \
    --model-evaluation-dataset "$ALDEN_EVALUATION_DATASET" \
    --evaluation-version "$EVALUATION_VERSION" --evaluation-root "$ALDEN_EVALUATION_ROOT" \
    --dpo-policy-dir "$ALDEN_EVALUATION_POLICY_DIR" \
    --dpo-reference-dir "$ALDEN_EVALUATION_REFERENCE_DIR" \
    --dpo-checkpoint-format "$ALDEN_EVALUATION_CHECKPOINT_FORMAT" --json
  if [ -n "${ALDEN_EVALUATION_STATE_ROOT:-}" ]; then
    set -- "$@" --state-root "$ALDEN_EVALUATION_STATE_ROOT"
  fi
  if [ -n "${ALDEN_EVALUATION_ADAPTER_DIR:-}" ]; then
    set -- "$@" --dpo-adapter-dir "$ALDEN_EVALUATION_ADAPTER_DIR"
  fi
  "$@"
fi

/bin/sh "$ROOT/scripts/build-alden-voice-audio.sh"

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

python3 "$ROOT/scripts/alden_build_receipt.py" record --root "$ROOT" --expected-key "$ALDEN_BUILD_INPUT_KEY"
printf '%s\n' "$APP"
