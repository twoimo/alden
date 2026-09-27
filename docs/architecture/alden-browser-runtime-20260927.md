# Dedicated Alden browser runtime

Date: 2026-09-27 KST. No KakaoTalk test message is sent by these checks.

## Runtime contract

Browser jobs select the fixed interpreter at `~/Library/Application Support/openkakao/runtimes/browser/bin/python3.11`. Snapshot, settings and other menu jobs retain the existing menubar interpreter. Voice retains its separate runtime. There is no installed runtime override or fallback to a checkout venv.

The provisioner pins CPython **3.11.16** for macOS arm64, browser-use **0.13.10**, Playwright **1.63.0**, Chromium/headless revision **1243**, and ffmpeg revision **1011**. It verifies the CPython archive and frozen requirements SHA-256, checks archive traversal and entry types, installs only local hashed wheels with `--no-index --require-hashes`, and publishes a new runtime directory after readiness checks. An existing destination is refused rather than overwritten. Chromium is copied to that runtime's own `ms-playwright` directory.

The Rust bridge validates the fixed interpreter and browser path, supplies `PLAYWRIGHT_BROWSERS_PATH` and `ANONYMIZED_TELEMETRY=false`, and retains Python `-E -B -s`, installed resource validation, stdin tasks, output/timeout limits and the existing emergency-abort grace. The menu entrypoint redirects Python browser progress output to stderr before printing its single JSON result. Browser inference remains bound by the existing loopback-only adapter; this runtime change does not add remote inference.

## Provisioning

Prepare the upstream CPython archive returned by `scripts/alden-browser-runtime.sh source-url`. Prepare a wheelhouse using a working CPython 3.11 pip:

```sh
python3.11 -m pip download --require-hashes --only-binary=:all: \
  --dest /private/tmp/alden-browser-wheelhouse \
  -r browser/requirements-runtime.txt

sh scripts/alden-browser-runtime.sh preflight \
  browser/requirements-runtime.txt /private/tmp/alden-browser-wheelhouse \
  "$HOME/Library/Caches/ms-playwright"

sh scripts/alden-browser-runtime.sh install \
  /private/tmp/alden-cpython-3.11.16.tar.gz \
  browser/requirements-runtime.txt /private/tmp/alden-browser-wheelhouse \
  "$HOME/Library/Caches/ms-playwright"
```

The source Playwright cache must contain the three pinned revisions. Provisioning neither restarts the app nor starts a browser or Kakao worker. Updating an existing runtime requires a separately reviewed replacement procedure; the installer deliberately does not provide one.

## Parent review and focused checks

The native Web child used `chatgpt-web/gpt-5.6-sol`, `xhigh`. Launcher checkpoints for `4f8f5fa21a42-39dd7be5` confirmed send acceptance and a visible response. The child then ended with the transport error `ChatGPT changed a completed text block that was already streamed to Codex`; it did not provide a completed final answer. The parent preserved the partial patch, reviewed it, corrected archive-case ordering so `python/../` cannot pass the `python/*` allowlist, added a traversal regression and libpython architecture validation, and continued integration locally. No base-model substitution was used.

Focused checks on the installed voice CPython 3.11 runtime and the desktop Rust target:

- Runtime payload/preflight tests: **6/6**, including tampered requirements, traversal and wrong/symlinked browser revision.
- Menu browser action tests: **5/5**, including stdout isolation.
- Rust Python bridge tests: **69/69**, including fixed action-specific routing, stdin task transport and abort deadlines.

The downloaded wheelhouse contains **110 wheels**, **98,209,110 bytes**. All downloads passed the frozen requirements hash contract. That is disk payload, not resident memory or an improvement measurement. Actual runtime provisioning and installed-app result readback are recorded below when completed; these source checks alone do not establish installed-app operation.

## Local provisioning readback

The parent provisioned the previously absent fixed runtime without overwriting the menubar or voice directories. The new runtime readiness probe returned `ready=true`, both exact package versions, `chromium_installed=true`, and adapter binding `browser`. This verifies installed dependency/binding readiness. An actual installed-app browser job and the Rust routing deployment remain separate checks.
