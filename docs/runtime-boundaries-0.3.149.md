# Affinity authority, pin geometry and attachment controls (0.3.149)

The graph driver previously treated identical node/edge/pair values as the same snapshot even when affinity presence, state, input key or profile key changed. This could reuse old membership metadata or an old worker engine under new evidence authority. `ALD-WORKER-SCOPE-004` now includes these authority fields in snapshot identity, drops checkpoints and replaces the persistent worker on authority changes. Completed coordinates remain a geometric prior; they do not retain old labels or authority. In-flight key mutation, pending reflow, same-authority coalescing, hidden work and explicit clear are covered.

The solver's public pins were exact while its spring calculations used origin-clamped pin proxies. `ALD-PIN-FRAME-003` / F02 now uses authoritative pin geometry. Extreme connected components scale all force terms together and use compensated accumulation for cancelling huge attractions. Sparse collision cells and single-focus-relative centroids avoid the numeric-grid exception; pins too far from movable contacts remain real spring endpoints and are excluded only from impossible local contacts. Limits, source records and exact public pins are retained. Offsets below floating-point spacing at astronomical coordinates remain unrepresentable.

The unpinned numeric-grid collision kernel is separate from the special pinned path. The final source preserves all 56 tested no-pin lifecycle results and checkpoints exactly. An initial generic implementation showed a synthetic median regression; its receipts and the intermediate unsuccessful micro-optimization are retained. After kernel specialization, 24 alternating baseline/candidate Node cold solves produced:

| Synthetic input | Baseline median | Candidate median | Baseline p95 | Candidate p95 |
|---|---:|---:|---:|---:|
| 128 nodes, relation groups | 6.98ms | 6.08ms | 10.84ms | 8.37ms |
| 2,048 nodes, relation groups | 118.38ms | 102.55ms | 149.97ms | 113.45ms |
| 2,048 isolated nodes | 69.44ms | 65.80ms | 95.74ms | 71.33ms |

These are same-process synthetic solver measurements and exact-output checks. They are not whole-app, field, FPS/GPU/battery or semantic-gold measurements. Background load affects absolute timing; the paired comparison scope is explicit.

The existing native file inputs now have Korean photo/document action labels, shared button styling and a visible keyboard focus ring. Native input IDs, accepted types, byte limits, change events, disabled state and selected-file previews remain in place. The input itself stays the hit target; no new attachment transport or picker API is introduced.

## Evidence and delivery gates

The driver owner reproduced 19 new failures, then passed 39 driver and 51 integration tests. The pin owner passed 61 engine and 39 driver tests. Main integration after performance specialization passed all 971 UI tests and the 56-state exact baseline comparison. Source and raw receipts are under `outputs/worker-authority-20261018/`, `outputs/pin-geometry-fix-20261018/` and `outputs/runtime-boundaries-20261018/`.

Before this update, installed 0.3.148 completed nine actual native image/file/link inputs on the saved Google Gemini 3.8 Flash high/manual route: image color and visual followup, prior number recall, UTF-8 document and CSV values, a pinned public document URL and its snapshot followup. The private SQLite ledgers confirm all nine requests completed in their original conversations. Three additional real generations exercised controlled post-inference ordering: a superseded answer could not overwrite the latest answer, and cancellation committed no assistant message. One earlier harness alias assertion failed after a real generation; its original evidence is retained. Requested route and returned basename are separate observations.

These media tests use owned programmatic WKWebView inputs or controlled process ordering, not physical file-picker/microphone/TTS/sending proof. They use isolated derived state and do not certify all media types, URLs or production workloads. Evidence is under `outputs/media-installed-20261018/`.

Canonical signed build, installed file/configuration reconciliation, native graph/focus/attachment-control captures and exact-source CI remain delivery gates for this version. The completed ChatGPT thesis input predates this delta; the original Gemini full report and final reciprocal reverse audit remain incomplete. Neither implementation readiness nor local installation establishes the whole 18-part objective or public notarized release.
