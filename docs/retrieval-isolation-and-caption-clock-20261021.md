# Separate retrieval defects and validate caption clocks

The caption importer previously accepted impossible seconds and reversed intervals because its timestamp check only matched a prefix-shaped clock. `[00:60 - 01:03]` and `[02:30 - 02:29]` could become collected source text. The importer now requires ASCII `MM:SS` seconds in00–59 and an end no earlier than its start. Minute counts may exceed59, rounded zero-duration cues and overlapping cues remain accepted, and valid raw text is retained exactly. This is structural validation; it neither repairs uncertain source timings nor verifies them against audio.

The new failing reproduction covers three invalid inputs and requires the prior collector checkpoint to remain unchanged. The affected video/acquisition/snapshot/realtime suites pass49 tests after the fix. All ten currently declared local video snapshots produce exactly the same normalized records as the installed0.3.207 parser, with unchanged inputs and zero source-store writes. These are scoped fixture and local-file integration checks, not proof that every upstream timestamp is accurate. Source overlaps or anomalously long positive intervals remain explicitly unverified.

## Retrieval isolation

The unchanged17-query contract and its saved E5 query vectors were compared through four methods on the same source/cache read snapshots with rotating method order. All68 retrieval calls execute RRF; new query embeddings are zero. The independently disabled variants separate complete original title-word validation from matched retained excerpts and fair context budgets. Existing permission/current-version/raw-hash/numeric-boundary/cancellation/verification-budget/pin/latest/context-size contracts remain required.

Eight Korean reference questions, exact source answers, supporting timestamped clauses and explicit unknowns were fixed before evaluating answer context. They are assistant-authored source-grounded contracts, not independent human semantic gold. Caption wording, uncertain transcription and speaker claims are not promoted to verified facts.

| Mechanical measure | Baseline | Title only | Excerpt only | Combined |
| --- | ---: | ---: | ---: | ---: |
| Native-title source emitted | 0/8 | 8/8 | 0/8 | 8/8 |
| Caption source emitted | 6/8 | 6/8 | 8/8 | 8/8 |
| Caption expected-source fact contains all original query words | 4/8 | 4/8 | 8/8 | 8/8 |
| Reference answer anchors in the expected-source context | 4/8 | 4/8 | 8/8 | 8/8 |
| All required supporting clauses in that context | 4/8 | 4/8 | 8/8 | 8/8 |
| Original control source rank | 21 | 1 | 21 | 1 |

All79 emitted excerpt checks match verified body/version/scope/coordinates, and responses remain within8,000 characters. No wrong-source answer-anchor decoy occurred in these eight context contracts. This is not a measured wrong-source answer rate: no answer was successfully generated.

## Actual generation remains incomplete

The installed configured route was verified as manual `google-antigravity/gemini-3.8-flash`, high. Thirty-two method/question cells were prepared and identical prompt bytes grouped before dispatch. The first actual generation request returned HTTP429 and `routed_llm_quota`; generation stopped with zero completed responses, no retry or model substitution. The installed catalog subsequently displayed `quota`, and the saved model selection remained unchanged. No account/model/global quota scope is inferred beyond that failed route observation. Generated-answer accuracy and unsupported/wrong-source answer rates are unavailable.

The original approved terminal204 external-review request also remains unstarted behind its separate shared model-allowance hold. The supported coordinator exposes no quota-correction command and the meter lacks numeric allowance/reset and owner attribution. The request retains its approval and exact identity; its own queue readiness was marked blocked with the precise resumption condition so it cannot obstruct unrelated ready work. No quota JSON was edited, no provider limit bypassed and no replacement research dispatched. The ChatGPT→Gemini→ChatGPT reports remain incomplete.

The retrieval candidates remain outside the runtime and are not promoted. Their release still requires actual answer evaluation, broader independent semantic/context checks and the required external review. The caption clock guard is a separate ordinary input-integrity correction; no ranking, embedding, identity, graph relation or merge policy changes with it. Runtime installation and scheduler adoption require their own artifact/readback receipts; the source/test results above do not claim those delivery stages.

Private source contracts and results remain under `outputs/retrieval-isolation-207-20261021/`; the previous runtime preservation/staging receipts are under `outputs/clock-guard208-20261021/`. No captions, video IDs, prompts, query vectors or runtime state are published here. The full18-item objective, ST01–13 and Cursor gates remain active.
