<div align="center">

# openkakao-bot

**A private assistant for the KakaoTalk macOS app.**

[![CI](https://github.com/twoimo/openkakao-bot/actions/workflows/ci.yml/badge.svg)](https://github.com/twoimo/openkakao-bot/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/twoimo/openkakao-bot?color=blue&logo=github)](https://github.com/twoimo/openkakao-bot/releases/latest)
[![Platform](https://img.shields.io/badge/platform-macOS-000000?logo=apple&logoColor=white)](https://github.com/twoimo/openkakao-bot)
[![Rust](https://img.shields.io/badge/core-Rust-dea584?logo=rust&logoColor=white)](https://www.rust-lang.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

<p align="center">
  <a href="#features"><b>Features</b></a> &bull;
  <a href="#architecture"><b>Architecture</b></a> &bull;
  <a href="#model-support"><b>Model Support</b></a> &bull;
  <a href="#quick-start"><b>Quick Start</b></a> &bull;
  <a href="#configuration"><b>Configuration</b></a> &bull;
  <a href="README.ko.md"><b>한국어 문서 (Korean)</b></a>
</p>

</div>

---

`openkakao-bot` is the project and CLI name; **Alden** is its macOS menu-bar assistant. It helps you find context and prepare replies in KakaoTalk. Conversation data and AI requests stay on your Mac; replies go through the KakaoTalk app already installed there.

<h2 id="architecture">Architecture</h2>

Alden is a macOS menu-bar assistant. Its compact display is drawn with Tauri v2 and Three.js. KakaoTalk messages, local search, and model requests stay on this Mac; sending a reply uses the installed KakaoTalk app. Press **⌘⌥⇧Esc** from any app to latch the global emergency abort.

See the [end-to-end local system map](docs/architecture/alden-system.html) and its [Archify source](docs/architecture/alden-system.architecture.json). The map shows the desktop shell, local tools, model boundary, and read-only conversation index in one view.

### The animated core

1. Tauri reports when the small Alden panel is visible.
2. Three.js draws the warm gold core and updates its rings while the panel is open. The center sphere uses a fixed-light `ShaderMaterial`; a paired local WebGL run measured 3.4% lower median CPU submission at 15 fps and 2.5% lower at 30 fps (about 3–5 μs per frame), with 8 draw calls in both variants. Pixel output matched in 11 of 12 theme, activity, and scale cases; the remaining case differed by one 8-bit channel level. These are CPU submission measurements, not GPU power or battery measurements.
3. Reply, voice, and sync activity change the rings' rotation and the core's pulse.
4. Hiding or closing the panel pauses drawing and status updates; reopening it resumes them.

See the [render lifecycle](docs/architecture/alden-three-render-lifecycle.html) and [activity-to-motion map](docs/architecture/alden-core-load-mapping.html).

Source-rendered capture (latest dark-theme source render; not a live installed-app capture):

![Alden champagne-gold spherical core](docs/architecture/alden-render-panel.dark.png)

### Finding related conversations

1. The app copies KakaoTalk's database and its side files to a temporary snapshot, then checks that the copy is stable.
2. It opens only the snapshot in read-only mode. A failed or unstable copy never falls back to the live database.
3. It normalizes shortened names and searches by both words and meaning. When both searches are available, reciprocal-rank fusion (RRF) combines their results; otherwise it keeps the working word-search results.
4. It stores people, topics, and their relationships as three-part facts (person — relationship — topic). Selecting a person or topic smoothly focuses nearby items, with at most 24 visible at once. This reads the existing index and does not start a new database copy or reindex.

The [Threads reference](https://www.threads.com/share/BBIeDkkHei/) argues that a knowledge node should be a unit worth reusing on its own; links should carry the context that would otherwise be lost when notes are split. The linked [OSK System](https://github.com/lpaiu-cs/osk-system) is the related open-source project. This app applies that principle by retaining each message as source evidence, deriving canonical entities and unique subject–relation–object triples, and keeping bounded message evidence IDs for traceability. Room, person, and topic nodes provide navigable neighborhoods. Drill-down starts at two relationship steps, limits each step to ten neighbors, and caps the view at 24 nodes (up to three steps when expanded). BM25 remains available when local embeddings are unavailable; RRF is used when both ranked candidate lists are ready.

See the [GraphRAG search sequence](docs/architecture/graphrag-search-sequence.html) and [conversation-map drill-down](docs/architecture/openkakao-graphrag.html).

### KakaoTalk reply turns

Each incoming KakaoTalk row remains a durable queue item. Consecutive rows from the same room and numeric author, no more than 15 seconds apart, are assembled into one reply turn, capped at six rows and 8 KiB. The 15-second settle interval lets the newest fragment arrive before inference; a successor supersedes an earlier job only when its saved burst IDs include that earlier row. The assembled prompt keeps each included message in order. Author changes, older attachments, and size/count limits end the burst. Legacy v1 queue records retain their original 2-second interpretation.

Empty Kakao emoticon rows (message types 12, 20, and 22) enter the same durable queue as `[이모티콘]` instead of being acknowledged as empty input.

Punctuation-only follow-ups such as `???` and short referential questions such as `뭐지 저건` are treated as pointers to the current thread. When recent messages give a topic, the worker answers from that context or asks one brief, topic-specific clarification tied to its recent-message evidence; a generic “I don't understand” reply is rejected. If local generation returns malformed output, referential follow-ups use this grounded clarification directly, while other malformed outputs are retried at most once per durable queue event. Factual questions whose answer is absent keep the existing explicit unknown-answer path.

### Voice conversation

Voice is currently unavailable in the checked-out app: the wake-word model failed its release gate, the settings control is disabled, and the Rust bridge rejects voice-session startup. The source keeps the intended local pipeline bounded to four recent turns and 600 characters per message, with a ten-minute idle reset; those limits do not mean live microphone, STT, model, or TTS use has been validated.

Before loading Whisper or Qwen3-TTS, a local-only admission check requires at least 8 GiB or 10 GiB of reclaimable RAM respectively and 2 GiB of free swap. If either probe is unavailable or the budget is low, the voice session reports the condition and does not load the model. A synthetic local voice run on 2026-09-24 reached the safety stop before a complete turn; end-to-end voice remains unverified on this host.

Local MLX replies reject an explicit response model ID that differs from the requested model, after removing an optional `mlx/` transport prefix. Responses without a model ID remain accepted for gateway compatibility; generation readiness is still tracked separately.

The settings UI marks voice status unavailable when its heartbeat is more than five minutes old, instead of presenting a stale `wake_listen` state as live.

### Other architecture diagrams

- [Tauri menu-bar architecture](docs/architecture/alden-openkakao-units1-4.html)
- [Local MLX request drain and model swap](docs/architecture/alden-model-request-drain.html)
- [Offline DREAM-RSI review loop](docs/architecture/dream-rsi-provenance-loop.html)
- [Browser-use lifecycle](docs/architecture/alden-browser-use-lifecycle.html)

## Source readiness

**Alden 0.1.5 was rebuilt and installed locally on 2026-09-27 KST.** The installed app matched the built bundle byte for byte, one Alden process was running, and the installed backend snapshot reported three reply-ready rooms. The newly baked room-worker runtime also matched all 20 packaged asset digests; its 18 script/data assets and configuration matched their current sources, while the packaged CLI matched the separately signed stable CLI. The active watchdog used that new runtime, and a later host readback reported **3/3 rooms ready**, with no pending database gaps or in-flight candidates. See the [dated installation evidence and remaining checks](docs/architecture/alden-install-20260925.md). This does not measure live conversational latency or KakaoTalk delivery.

The earlier synthetic Chromium rendering passed **32/32 checks**. At the settings window's 960 px width, its measured columns were 553.719 px and 342.266 px with no horizontal overflow; a simulated hidden-window signal produced zero frames over 1.503 seconds. These results cover the built frontend with a stubbed Tauri bridge. The review screenshots and JSON use fictional chat and graph data. A later settings layout change gives the knowledge graph and recent-answer list the full available width; its CSS contract passed 10/10 checks and the frontend built, while a local Chrome screenshot confirmed the full-width graph card. The full synthetic Chromium suite has not been rerun for that layout change.

The existing mixed 4/8-bit Flash-Next checkpoint could not restart when its memory preflight required about **77.1 GB** and found only **51.8–54.4 GB** available. The current Alden deployment uses Qwen3.8 27B; its model catalog reported **18,196,477,654 resident bytes**, and one earlier synthetic localhost request returned HTTP 200 with one completion token in **0.553 s**. A smaller Flash-Next 3.3bpw checkpoint has since been downloaded at a fixed revision and answered a synthetic request on a separate localhost server while 27B remained loaded. That temporary server was stopped after the check. See the [dated Flash-Next local check](docs/architecture/alden-flash-next-local-eval-20260927.md) for file counts, memory preflight and measured timings. The MLX management job also completed a scheduled check with exit code 0. These observations do not establish normal reply latency, Alden model selection, voice operation, or a public release. The three bundled menubar bytecode files are pinned in this repository and hash-checked during packaging.

The `Alden Desktop Release` workflow now gates macOS arm64 packages on Developer ID signing, Apple notarization, and published-asset checksums. As of 2026-09-27, it has not produced a release: this repository has no configured signing/notarization secrets, and the local keychain has only a development identity. The app ZIP needs a separate CPython 3.11 runtime on a fresh Mac; the workflow now packages a hash-pinned, offline-installable menubar runtime sidecar. On PR commit `0c6933c`, **4/4 CI jobs passed**, including an unsigned macOS arm64 bundle build and an offline sidecar install into an isolated temporary home. That sidecar does not provision MLX models or voice dependencies and does not inherit the app's Apple notarization. See the [desktop runtime contract](desktop/README.md). A 2026-09-27 read-only queue audit separated **9 proactive GeekNews sent** jobs from **2 conversation sent** and **21 conversation skipped** jobs. All 32 predated the 03:00:58 worker cutover. The latest 150 locally readable KakaoTalk rows in each of the three active rooms also predated it, so no post-cutover reply-latency or skip-rate improvement is established. See the [queue timing audit](docs/architecture/alden-install-20260925.md).

Voice startup is deliberately blocked until a wake model passes its release gate. The current experimental wake model accepted 3/3 synthetic positive clips and falsely accepted 1/3 synthetic negative clips (33.333% at threshold 0.65); there are no human-speaker or microphone/room trials. The candidate is excluded from the app bundle. These source and evaluation facts do not establish a working microphone or a complete voice conversation.

### Historical runtime observations (2026-09-24–25)

At 23:43 KST on 2026-09-24, read-only checks returned HTTP 200 from local MLX `/health` and `/v1/models`. Flash-Next was loaded (75.3 GB resident); Qwen3.8 27B and Qwen3-TTS were unloaded. One bounded local Flash-Next generation returned `OK` in **56.849 s**. This verifies a single short local generation, not normal conversational latency; the earlier 45.060-second zero-token disconnect remains a separate failed attempt. The loaded model has no embedding capability, so live GraphRAG remains BM25-only and dense/RRF is unavailable.

The post-probe session-monitor readback was healthy at that time: all three configured room workers were ready, their reply model was available, delivery was enabled, and there were no pending database gaps. Free swap was **1,322.19 MiB**, **725.81 MiB** below the 2,048 MiB voice-model admission threshold. The observed voice heartbeat was over 25 hours old, so this check did not verify Whisper/TTS loading or a complete wake→STT→LLM→TTS turn. The installed immutable worker matched the source snapshot at the time by SHA-256; that comparison does not cover subsequent edits. The diagnostic request was local-only and used no conversation data; no message was sent and no model was loaded or swapped. See [engineering status](docs/engineering-status.md) for queue counts, historical confusion-reply replay, and measurement limits.

At 23:36 KST on 2026-09-24, Tauri app v0.1.5 was installed and its LaunchAgent process was running. All 27 installed bundle files matched that build. The configured global emergency shortcut is **⌘⌥⇧Esc**; the adjacent **⌘⌥Esc** chord belongs to macOS Force Quit. This bundle check did not exercise a physical global shortcut event.

At 00:28 KST on 2026-09-25, a later read-only host check returned `healthy=false`: two of three room workers were ready and one was fenced during a transient context-sync failure. The primary reply room remained ready, with delivery enabled and no pending gaps. That observation predates the current source edits and does not verify their deployment.

The detailed implementation notes and dated verification records are kept in [engineering status](docs/engineering-status.md). They describe source checks, automated tests, and live runtime observations separately.

<h2 id="features">Features</h2>

- **Private processing**: Conversation search and AI replies run on this Mac.
- **Safe conversation reading**: The app reads a temporary, read-only copy of KakaoTalk's local data.
- **Conversation map**: Select a person or topic to see nearby names and related messages.
- **KakaoTalk integration**: Replies are entered in the KakaoTalk app.
- **Protected sending**: The app pauses when it cannot confirm which reply or destination is safe.

<h2 id="model-support">Model Support</h2>

The menu app offers a fast local model for everyday replies and a larger local model when requested. It does not automatically send conversation data to a cloud AI service. Exact model names and setup details are in the [Korean setup guide](README.ko.md); current source gates and dated runtime evidence are in [Source readiness](#source-readiness).

<h2 id="quick-start">Quick Start</h2>

### 1. Build from Source

```bash
git clone https://github.com/twoimo/openkakao-bot.git
cd openkakao-bot
cargo build --release
```

### 2. Permissions

In macOS **System Settings -> Privacy & Security**:
- **Full Disk Access**: Grant to your Terminal (or `Alden.app`) to read local database files.
- **Accessibility**: Grant to allow typing replies into KakaoTalk.

<h3 id="configuration">3. Configuration</h3>

```bash
mkdir -p ~/.config/openkakao
cp config.example.toml ~/.config/openkakao/config.toml
```

Minimal `~/.config/openkakao/config.toml`:

```toml
[model]
privacy_mode = "local"
allow_egress = false
provider = "mlx-serve"

[auto_reply]
# Allowed chatrooms: ["bind:<chatId>:<exactOnScreenName>"]
chats = ["bind:123456789012345:TeamChannel"]

# Your display name in KakaoTalk
self_nickname = "Your Name"

# Recommended on-device engine (MLX), not Gemma / llama.cpp / Ollama
reply_runner = "/absolute/path/to/installed/opencodex"
reply_runner_kind = "opencodex"
reply_model = "ddalcu/Qwen3.8-27B-MLX-Serve-4bit"
```

`reply_runner` is a validated transport placeholder for this local MLX profile and must point to the installed `opencodex` executable. The exact 27B and Flash-Next IDs, with or without the `mlx/` prefix, are allowlisted. The current 27B deployment passed one synthetic localhost completion; Flash-Next can be selected explicitly when it is resident and ready. This does not establish normal conversational latency.

### 4. Run

```bash
# Verify environment and discover chat rooms
./target/release/openkakao-cli doctor
./target/release/openkakao-cli local-chats

# Build and install the primary Tauri menu-bar UI
sh scripts/build-alden-desktop.sh
sh scripts/install-alden-desktop.sh

# Start the installed LaunchAgent without rebuilding or reinstalling
sh scripts/start-auto-reply-menubar.command
```

Privacy paths, KakaoTalk table names, and Korean operator notes live in [README.ko.md](README.ko.md). Do not commit chat databases, `context.sqlite3`, `knowledge-graph.sqlite3`, or credentials.

---

## License

[MIT License](LICENSE)
