# macOS Python/Rust resource contract

**Alden** is the menu-bar product name. `openkakao-cli` and this repository
retain the OpenKakao project identifiers.

The running executable selects the layout, independently of the current directory:

```text
Alden.app/Contents/
  MacOS/openkakao-alden-desktop
  Resources/
    scripts/auto-reply-menubar.py
    scripts/alden_voice.py
    scripts/<fixed support modules and CPython 3.11 bytecode>
    scripts/auto-reply-operator-prompts.json
    bin/openkakao-cli
```

Wake-word candidates live under `voice/models/experimental/` and are excluded
from the app bundle until held-out human-speech evaluation clears the release
false-accept gate. The desktop voice-start command remains disabled meanwhile.

`src-tauri/src/resource_layout.rs::DATA_FILES` is the allowlist; `tauri.conf.json`
maps every staged file to its exact destination. `build.rs` copies only these
files and the CLI from the checkout's `target/debug` or `target/release`, according
to the desktop build profile. It never traverses/copies `.venv*`, the home
directory, credentials, state, logs, caches, or arbitrary script directories.
Missing bytecode or CLI inputs stop packaging. The three frozen CPython 3.11
artifacts are pinned in this repository. The packaging step verifies their
presence but does not regenerate them.

Any `.app`, including an unsigned debug bundle, uses only its own Resources.
An incomplete/unsafe bundle never falls back to `CARGO_MANIFEST_DIR` or PATH.
All payload files and every parent component must be present and non-symlink;
files must be regular, nonempty and readable; CLI/interpreters must be executable.
Validation runs again before each bridge invocation. This is a local filesystem
boundary, not a signature verifier or protection against concurrent modification
by the same user. Signed distribution and runtime provisioning remain separate.

Python itself is **not bundled**. Installed apps require separately provisioned
CPython 3.11 runtimes at these fixed paths (real executables, no symlinks):

```text
~/Library/Application Support/openkakao/runtimes/menubar/bin/python3.11
~/Library/Application Support/openkakao/runtimes/voice/bin/python3.11
```

Provision full runtimes, not just copied interpreter binaries. The menubar needs
CPython 3.11 for its frozen bytecode; the voice runtime needs the dependencies in
`voice/pyproject.toml`. Python runs with `-E -B -s` (ignore Python environment,
no bytecode writes, no user site packages). Missing runtimes report
`python_environment_missing_or_unsafe` / `voice_environment_missing` before
spawn. No runtime/dependency/model download or microphone access occurs in the
packaging step. A conventional symlink-based venv is deliberately rejected.

Outside a bundle, debug builds may use the checkout and these development/test
overrides: `OPENKAKAO_RESOURCE_ROOT`, `OPENKAKAO_MENUBAR_SCRIPT`, `OPENKAKAO_BIN`,
`OPENKAKAO_PYTHON`, `OPENKAKAO_STATE_ROOT`,
`OPENKAKAO_LOGS_DIR`. Script/CLI overrides stay within the chosen resource root;
interpreter overrides must be absolute safe executable paths. The menubar uses
the existing uv CPython 3.11 path. Voice runtime provisioning is dormant while
the wake-model release gate is closed; use a copied-executable
voice environment when developing. Release executables outside a bundle fail
closed. Installed apps ignore these overrides.

State still uses `~/Library/Application Support/openkakao/auto-reply` (legacy
`bujamentor` enrollment fallback); logs still use `~/Library/Logs/AutoReplyMenu`.
Voice output and abort state stay under state, not Resources. Snapshot/action
timeouts (25/8 seconds), cancellation, and the 4 MiB stdout limit are unchanged.
Voice remains explicitly started, with its existing abort protocol; it does not
inherit the short snapshot timeout.

From the repository root, build the primary menu-bar bundle with the repository
script. The script stages the matching release CLI first and uses an unsigned
bundle unless `OPENKAKAO_SIGN_IDENTITY` is explicitly supplied:

```sh
sh scripts/build-alden-desktop.sh
```

Install the resulting app and its LaunchAgent only after reviewing the bundle.
The installer checks the fixed menubar CPython 3.11 runtime before it backs up
or changes any app or launchd state; a missing, unsafe, or nonworking interpreter
stops the cutover:

```sh
sh scripts/install-alden-desktop.sh
```

For a debug bundle, use `cd desktop && npm run tauri -- build --debug --bundles
app --no-sign` after preparing the debug CLI. Native macOS builds are supported
here. The `Alden Desktop Release` workflow builds a macOS arm64 app for exact
`alden-vX.Y.Z` tags and publishes it only after Developer ID signing, Apple
notarization, stapling, checksum verification, and GitHub asset readback. It
requires six `ALDEN_APPLE_*` signing and notarization secrets. Without them,
the release stops before packaging. The published `.app` ZIP does not provision
CPython, MLX models, or voice dependencies: a fresh Mac must satisfy the runtime
paths and requirements above before the menu bridge or voice path can run. The
wake-model release gate remains closed. A signed package by itself is not an
end-to-end product check.

Focused verification: `cargo test --manifest-path desktop/src-tauri/Cargo.toml`,
and `npm test` / `npm run build` in `desktop`. Fixtures exercise moved installed
layouts, missing files, symlinks (including parents/dangling links), wrong types,
permissions, dev-only fallback/overrides and exact JSON resource declarations.
Process tests run only short synthetic Python snippets. Building/inspecting the
unsigned `.app` does not launch it or establish live KakaoTalk, mic, model,
TCC, signing, installation, or send success.
