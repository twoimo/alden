# Actual graph/event component baseline — 2026-10-27

The ordinary collection graph page now retains its 512 relationship-group budget. A SQL CTE string previously overwrote the `overview` boolean, making the subsequent limit branch choose 4096 for ordinary pages too. The change gives that string its own local name. A genuine `read_action` regression with 40 synthetic nodes and 1560 directed relationships fails before the change and passes afterward: ordinary pages return 512 groups, overview pages retain 1560, and both preserve source totals, node identities and the activity checkpoint. No source permissions or traversal rules change.

Source base: `6e2b1fbd276c310714c5957993cb9accc21eb957`. Accepted collection module SHA-256: `6bfd9d5c7d03452868de69560e8d3991645aedd00e1a380a8fa74c701e99693f`. Task evidence is in `outputs/graph-actual-event-cost-20261027` outside the repository. Original labels, IDs, URLs, graph/journal packets and private proxy account labels stay there; they are not attached to the PR or external review.

## Actual permitted source capture

The genuine production `read_action` boundary read all five catalogued project scopes explicitly, using its default current production authority. It returned a bounded overview with 1984 nodes and 2693 directed relationship groups. Source-scope totals were 26651 nodes and 48355 stored relationship claim records; those are different denominators from displayed groups and are not independent corroboration counts. The response has another page and is not a corpus export.

The collection API accepts at most 1984 overview nodes. The UI ceiling of 2048 also reserves up to 64 memory nodes; this measurement neither fabricates that union nor pads the collection response. Native source completeness and semantic correctness are not established by `ok:true`.

All 27 observed SQLite connections opened existing regular files with URI `mode=ro`, set and verified `query_only=1` before returning to product code, and installed a mutation-denying authorizer. An OS sandbox denied network and writes outside this task's directory. Its denial behavior was tested against a separate owned fixture, whose bytes stayed unchanged; no write probe touched original data. The actual capture recorded zero denied network, Python-write, subprocess or SQLite-write attempts, and no model requests, model loads or SDK-health subprocesses. Root identity and all 129 Python source hashes matched before/after. These are process-bound read guards, not a claim that another owner stopped writing the live store.

The capture took separate read transactions. It is an as-observed artifact, not one immutable cross-call source snapshot; cross-store TOCTOU remains. A later activity query advanced 12 raw stages with zero activity items and an unchanged content revision. Sequence advancement alone is not a fresh node activity.

## Historical journal and actual consumer

Three bounded historical activity pages yielded 120 receipts, all timestamped before the capture. The bounded window did not reach the initial high watermark. A separate recent processing-history page returned 200 rows with older pages remaining. Processing rows and activity receipts have different schemas and must not be summed or interchanged. No historical timestamps, stream IDs or graph cursors were replaced.

The current production `CollectionActivity` consumed the unchanged captured graph checkpoint and actual subsequent tail response under a controlled timer harness. It requested the captured `after`/stream/limit, committed the 12-stage advance, and produced zero invented pulses. Stop removed all scheduled timers; restart scheduled one timer while retaining the committed cursor; reset forgot the checkpoint. Historical pages were inspected as historical evidence, not injected behind a forged graph checkpoint. No actual fresh added/revised receipt was present, so positive new-event display and event-to-render latency remain unverified. Controlled timers do not prove native hidden-window behavior.

## CPU solver baseline

The current production parser and overview selection supplied actual induced views to the current `SemanticLayoutEngine`. No nodes or edges were fabricated and no semantic affinity pairs were supplied. Node v26.12.0, macOS arm64; the observation clock was frozen. Each size used ten fresh engines, with the first sample retained separately and nine subsequent samples summarized below. Each pin sample used that engine's actual initial result, then pinned one actual node at its existing position. These are different operations, not a before/after implementation comparison.

| Actual nodes | Displayed groups | Initial solver median / empirical p95, ms | First-pin solver median / empirical p95, ms |
| ---: | ---: | ---: | ---: |
| 128 | 514 | 7.13 / 14.14 | 18.48 / 29.59 |
| 512 | 1967 | 19.54 / 25.78 | 45.85 / 51.03 |
| 1984 | 2693 | 59.54 / 64.98 | 110.82 / 132.79 |

All repeated frozen inputs had identical initial geometry hashes, finite anchors, correct admitted-node counts, exact pinned-position retention and unchanged input packets. The empirical p95 is the largest of nine samples; it is not a population estimate. The 1984 first-pin cost is an optimization candidate. It does not establish a UI input-latency failure or prove that a particular algorithm is its cause. Parser/overview overhead, Worker serialization, renderer frames, GPU, battery and native memory costs are not timed by these solver intervals. Node process memory/resource snapshots are process-only observations. There is no measured speedup, asymptotic bound or whole-application performance claim.

## Review and validation

- Native Codex `gpt-6-astra` / `ultra` traced the public source/authority/journal contract and independently reproduced the budget defect before implementing the bounded correction and regression. Parent reviewed the diff and preserved the baseline evidence.
- Project menu Python 3.11.9 / SQLite 3.46.0: 582 tests in 24 affected collection/entity/graph modules pass in separate interpreters, with zero failures/errors/skips. Earlier Python 3.12 runs hit the existing 3.11 frozen-menu bytecode contract; a worker Python 3.15 / SQLite 3.54 focused search error also reproduced before this correction. Those results remain recorded, rather than being relabelled as passes.
- Supplemental OpenCodex `google-antigravity/gemini-4-argon`, high: one canonical upstream send, HTTP 200, no recovery; request `ocx-96d821e0f23a2c119de4a37592c16116`. It substantively read supplied public code and anonymous counts/timings, found no concrete correction/harness P1/P2 defect, and suggested profiling the first-pin sparse-grid path. Its proposed grid condition is a hypothesis, not an accepted source edit: absence of extreme component scaling alone does not establish valid dense-grid coordinates. Full source profiling, numerical/pin regressions and matched comparison are required before adopting an optimization. No original graph/journal text or account labels were supplied.

Alden remained OFF; protected settings and services matched before/after capture. IAB was unavailable, and Computer Use denied the Codex application with the stated reason “safety reasons”; that UI path stopped. No alternate browser was used. This phase does not establish rendered WebGL/GPU, installed application, fresh positive event, model inference, notarization or release acceptance. Required formal web-Astra/Gemini/reverse audit holds remain separate from the supplemental review. Exact-commit CI and a static candidate are subsequent delivery checks, not facts established by this document's source commit.
