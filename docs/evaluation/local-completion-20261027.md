# Local response completion, 2026-10-27

The local text/voice adapter must return a completed visible answer before the
typed ledger stores an assistant message or the voice pipeline speaks it. At
base `2d1ddeba0a004f1ffb48e190605af60d443d2e6b`, actual `LocalMlxLlm` calls
incorrectly completed a visible SSE response with no terminal reason, a tool
call's preface, and a content-filtered partial response. Independent regression
tests reproduced the failure through the actual typed caller and persisted
history, using synthetic HTTP responses and temporary state.

The adapter now rejects structured `tool_calls` and legacy `function_call`
metadata, including malformed values, and their explicit terminal reasons.
This adapter has no tool dispatcher; it does not execute a tool or issue a
second generation. Literal tool markup in ordinary answer text remains text.
Null tool fields and an empty `tool_calls` list remain valid no-call values.

Visible SSE answers require an explicit terminal `stop`, followed by the
existing `[DONE]` marker. A usage-only trailer remains valid; another choice
after the terminal is rejected. Ambiguous multiple choices and an explicit
choice index other than integer zero are rejected for the single requested
answer; an omitted legacy index stays compatible. `length` retains its truncation error; other
explicit non-stop reasons fail as incomplete. Legacy non-stream JSON with a
missing/null finish reason remains compatible, so that case is not independent
proof of semantic completeness. Reasoning-only responses remain empty answers.

Two fixed error codes survive sanitization and durable failed-turn replay. The
composer explains the failure and retains the draft. The real voice pipeline
returns its existing generation error with zero TTS calls and no assistant row.
Model readiness, shared request lease, cancellation, identity checks, prompt
budgets and source validation remain in their existing paths.

Run the maintained regression and consumer checks from the repository root:

```sh
python3 -B -m unittest tests.test_alden_local_completion tests.test_alden_voice_turns tests.test_alden_voice_metrics tests.test_alden_chat_metrics tests.test_local_mlx_gateway -v
cd desktop
npm test -- src/__tests__/assistant-composer.test.ts
npm run build
```

The first independent baseline run had 33 tests: 14 passed and 19 failed,
including 67 failed subtest checks and zero errors. The final regression adds
seven cases for malformed terminal/message data, ambiguous choices and indices,
post-terminal choices, and the actual voice consumer. The three choice tests
also failed against the initial candidate before that gap was corrected.
Evidence is retained in project
`outputs/local-tool-roundtrip-20261027/`, including baseline logs, source pins,
affected-suite results and native Astra source review/model receipts.

These checks exercise product code with synthetic transport and temporary
history. They do not establish real model quality or a complete product tool
roundtrip. Current local generation uses the shared HTTP gateway; there is no
supported in-process inference/tool dispatcher path. The task-owned fixed 27B
checkpoint's earlier standalone inference is separate evidence. The observed
shared server and stale managed-owner state are preserved; no live model POST,
model load/adoption, service change, operational DB write or native app launch
is part of this phase. Installed UI, real voice/model/tool execution, external
audits and normal release remain separate unfinished gates.
