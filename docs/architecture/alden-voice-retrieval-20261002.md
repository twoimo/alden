# Voice retrieval — 2026-10-02

## Behavior

Alden 0.3.2 connects explicit voice knowledge requests to the published local KakaoTalk corpus and local E5/GraphRAG retrieval. The latest confirmed speech remains the final user message; retrieved documents are quoted JSON with original source roles, room identities, source event IDs and dates. The encoded JSON, including escaped markup, has an 8,000-character budget. Unclassified outgoing history is not promoted to human speech or confirmed assistant output.

Exact visible room names establish the room scope. Equal-name rooms require one clarification. A follow-up may inherit a contiguous knowledge topic; an intervening unrelated user turn ends that chain. A new explicit query establishes its own scope. Numeric room IDs resolve to account-scoped graph IDs. Global person entities supply only observations and source IDs from the selected room.

The shared HTTP transport admits only the fixed loopback model endpoints: 11234 for models/chat and an explicit embedding mode for 11236 models/embeddings. Per-turn/global cancellation closes the socket even while waiting for headers. Published-corpus search and scoped graph hydration receive cancellation through SQLite progress handlers; cancellation is checked again before inference.

## Evidence and limits

Before the final topic-chain/encoded-budget repairs, the real 0.3.2 source sequence used the existing loaded `ddalcu/Qwen3.8-27B-MLX-Serve-4bit` and `mlx-community/multilingual-e5-small-mlx@5030c7625865046d350eeea28f427d80353d0ac0`. It queried an agent-owned synthetic two-room corpus: Friday at 15:00 in the meeting room, Saturday noon in the movie room. The first request and follow-up both answered Friday at 15:00; neither included the other room's event. The unrelated arithmetic request returned 4 with retrieval `not_requested`. Retrieval took 58.638 ms and 30.467 ms, one observation per query. This is scoped sequence evidence, not Recall@k, a percentile estimate or a measured speedup.

The prior configured source suite passed 1,127 tests with 27 skips; native tests passed 91, Clippy passed and the UI build passed. The final topic-chain/encoded-budget repairs add focused regressions and require the new source/installation receipts before delivery is claimed. Three isolated installed 0.3.1 subprocesses separately completed six real STT→27B→TTS-file turns; see the [file-pipeline baseline](alden-installed-voice-file-20261002.md). Those checks exclude wake/VAD, human microphone, physical interruption and speaker playback.

The [voice cancellation diagram](alden-voice-cancellation.html) remains byte-identical to its delivered artifact, passes all nine showcase checks with zero errors/warnings, and has bounded browser evidence at 1440×900, 1600×1000, 1920×1080 and 2048×1320. Image review covered 1440×900 light and 2048×1320 dark: labels, arrows, panels and navigation are legible and do not overlap. Authored labels are Korean; the fixed viewer UI is English. Artifact, browser and perceptual evidence are recorded separately in the [verification receipt](alden-voice-cancellation.verification.json).

The loaded embedding service and the published corpus can be read independently of pending macOS app-data approval. Live Kakao source freshness, physical installed UI/hotkey/Retina checks, a released Korean wake model and Apple production signing/notarization remain separate gates. This change does not establish full goal completion or a production-worker cutover.
