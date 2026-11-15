# Alden agent guide

Alden ships `openkakao-cli`, a KakaoTalk for macOS auto-reply agent. The CLI reads the
local KakaoTalk SQLCipher database. It sends through macOS Accessibility (AX), and a Python
runtime under `scripts/` composes the replies. Every command takes `--json`. JSON goes to
stdout and diagnostics go to stderr.

## Layout

- `src/` holds the Rust crate. `src/main.rs` defines the clap CLI. Handlers live in
  `src/commands/` and the domain modules beside it (`ax_send.rs`, `safety/`, `context/`,
  `local_db.rs`, `geeknews/`, `loco/`).
- `scripts/` holds the Python auto-reply runtime: worker, service, supervisor, db-watch,
  session-monitor and menubar. Production runs a baked, frozen copy of these files, not
  the checkout.
- `tests/` holds Rust integration tests (`*.rs`) and Python `unittest` modules (`test_*.py`).
- `desktop/` is the Tauri desktop app (Vite + TypeScript front end, `src-tauri/` bridge).
- `macos/AutoReplyMenu/` is the legacy Swift menu extra, still built in CI.

## Checks

Run the gate that matches what you touched. These are the CI commands.

```bash
cargo fmt --check
cargo clippy --all-targets -- -D warnings
cargo test
python3 -m unittest tests.test_auto_reply_worker_mlx   # name the modules you touched
sh scripts/test-auto-reply-launchd-artifacts.sh
(cd desktop && npm test -- --run && npm run build)
OPENKAKAO_TAURI_SOURCE_CHECK=1 cargo test --manifest-path desktop/src-tauri/Cargo.toml --locked
```

- The CI Python list is in `.github/workflows/ci.yml`. A new Python test module only runs
  in CI after you add it there.
- Run Python tests with `HOME` pointed at an empty temp dir. `tests/auto_reply_hermetic.py`
  guards `tests.test_auto_reply_cli_runtime` and `tests.test_auto_reply_turn_holds`. Other
  modules have no such guard yet.
- `tests.test_auto_reply_menubar` needs the baked frozen `.pyc`, so it fails on a fresh
  checkout. That failure does not mean your change broke it.
- A Python change reaches production only after a new runtime is baked and installed.
  Say "source-only" when you have not done that.

## Never touch the live account while developing

Every write reaches real people on the user's real KakaoTalk account, and LOCO writes can
get the account banned.

- Tests use the fake database, process and AX adapters (`src/fakes/` and the Python fakes
  in `tests/`). No test sends, opens KakaoTalk windows, or loads a real model.
- Preview every write with `--dry-run --json` first. Run a real write only when the user
  asked for that exact message in that exact room.
- Do not edit `~/.config/openkakao/config.toml` or `~/Library/Application Support/openkakao/`
  unless the user asks.
- Never kill processes by name. Never adopt or stop a running supervisor, service or
  session-monitor. Never hide or close the user's Terminal windows.

## Send surfaces and gates

| Surface | Command | Gate in `config.toml` |
| --- | --- | --- |
| AX send (the product path) | `local-send <room> "<text>"` | `safety.allow_ax_send` plus the room in `safety.allowed_send_chats` |
| AX delete (`모두에게서 삭제`) | `local-delete <room> "<visible text>"` | same AX gates |
| Foreground auto-reply | `auto-reply --chat <selector>` | AX gates plus the runtime's own checks |
| LOCO writes (research only) | `send`, `send-me`, `delete`, `edit`, `react`, `mark-read` | `safety.allow_loco_write` |

- LOCO writes are quarantined. Do not enable `allow_loco_write` or `--unattended
  --allow-non-interactive-send` unless the user asks for LOCO research.
- Realtime watching uses `ax-watch`, not LOCO `watch`.

## Safe commands

```bash
openkakao-cli auth-status --json
openkakao-cli local-chats --json             # local DB, no network
openkakao-cli local-read <chat_id> -n 30 --json
openkakao-cli local-search "keyword" --json
openkakao-cli local-schema
openkakao-cli local-send "<room>" "<text>" --dry-run --json
openkakao-cli send <chat_id> "<text>" --dry-run --json
```

- `doctor --json` checks the token against Kakao's REST API when credentials exist.
  `chats`, `read <chat_id> --rest`, `friends` and `me` also call that API. They are
  read-only but not zero-risk.
- The `local-*` reads and `send-me --dry-run` can rewrite
  `~/Library/Application Support/openkakao/local-db-identity.json` when it is stale.
- `context-*` commands migrate whatever database they open. Point `--db` at a scratch copy
  when you are only testing.

## Auto-reply runtime

Always run the read-only preflight before starting a reply loop.

```bash
openkakao-cli auto-reply --chat '부자멘토멘티' --check --json
openkakao-cli auto-reply --chat '부자멘토멘티' --model gemini-3.6-flash
openkakao-cli auto-reply --chat 'name:부자멘토멘티' --chat id:123456789
openkakao-cli auto-reply --chat 'bind:417780809780519:부자멘토멘티' --check --json
```

- `--chat` repeats or takes comma-separated exact `id:`, `name:` or `bind:<chat_id>:<name>`
  selectors. `bind:` attests an already-open AX window for an unnamed group-room row.
- Interactive terminals may omit `--model` and pick the model with the arrow keys.
- `auto-reply` is foreground-only. `Ctrl-C` stops the workers it started.
- `auto-reply-host` (`--status`, `--bake`, `--tick --manifest <file> --state-root <dir>`) is
  the Kakao-blind unattended host run by the session monitor. It only opens Terminal and
  never owns AX send. The session monitor is the only unattended host.

## Product rules the code must keep

- Context and style search support the 최연우 persona. They are an adjunct to the reply,
  not the source of truth.
- GeekNews reads the official Atom feed and posts in three KST slots. It numbers a TOP5
  after a blank line, with a blank line between items. It persists seen items and slots
  only after the send is confirmed in the local database.
- An auto-reply worker send counts only after `confirm_worker_local_delivery` finds the
  outgoing row in the local database. An interactive `local-send` has no such confirmation.

## Conventions

- Conventional Commits (`feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:`).
- `rust-toolchain.toml` pins Rust. Keep new dependencies to a minimum.
- Record point-in-time verification in `docs/engineering-status.md`, not here.
