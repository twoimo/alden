# Alden 0.3.149: zoom reading and layout boundaries

Runtime source: `00999414c21ce3c351edd273cdefa46d4323bdb0`. Core implementation: `6926aae9bdfefa757b681548f7d8489815f411ca`. Installed locally with the existing Apple Development signing identity.

## Graph reading

The [zoom presentation rules](zoom-labels-0.3.148.md) continue in this release: four distant subjects, eight overview subjects, then viewport-local subjects and saved titles at closer distances. Topic text grows from 12 to 15px; title text stays 11.5–12px. Five percent hysteresis prevents rapid threshold switching. Selected and hovered entries retain priority, and the navy background remains.

The design follows [Sigma's label visibility rules](https://www.sigmajs.org/how-to/labels/styles-positioning/) and [Cytoscape's minimum zoomed font size](https://js.cytoscape.org/#style/labels). Alden retains its own projected labels and does not add either library.

| Installed viewport | Distant | Overview | Context | Detail |
|---|---:|---:|---:|---:|
| Default |4|8|15|24|
| Compact |4|8|12|13|

All eight installed graph captures were inspected directly. Label rectangle overlap, clipping and visible unpositioned labels were zero. These are owned WKWebView windows with controlled wheel input, not physical trackpad measurements.

## Corrected boundaries

- `ALD-WORKER-SCOPE-004`: affinity presence, state, input key and profile key now participate in authority identity. Changes discard old membership/checkpoints and replace the worker. Previous positions can seed geometry only. Nineteen new regression failures reproduced the old behavior; all 39 driver tests now pass. In-flight key mutation, pending reflow, same-authority coalescing, hidden work and explicit clear are covered.
- `ALD-PIN-FRAME-003`: multiple authoritative pins retain their actual spring endpoints. Common force scaling, compensated accumulation and sparse collision cells handle extreme finite coordinates. Pins too far from movable contacts are excluded only from impossible local contacts. Public pin coordinates stay exact. The engine has 61 passing tests. Offsets below floating-point precision remain unrepresentable; screen position does not establish semantic truth.
- A common collision kernel avoids the intermediate candidate's measured no-pin regression. Full results and checkpoints match the previous engine for 56 lifecycle states. Both the initial generic implementation's regression and the unsuccessful micro-optimization receipts remain preserved.
- Native audit captures now require the selected graph to finish camera, physics, worker and target transitions. Earlier transient captures are retained; their rectangular concentration is not recorded as a confirmed product defect.
- Photo/document picker controls use Korean labels and visible keyboard focus styling. Native file inputs and existing input limits remain.

Twenty-four alternating baseline/candidate Node cold solves produced:

| Synthetic input | Baseline median | Candidate median | Baseline p95 | Candidate p95 |
|---|---:|---:|---:|---:|
| 128 nodes, relation groups | 6.98ms | 6.08ms | 10.84ms | 8.37ms |
| 2,048 nodes, relation groups | 118.38ms | 102.55ms | 149.97ms | 113.45ms |
| 2,048 isolated nodes | 69.44ms | 65.80ms | 95.74ms | 71.33ms |

These are same-process synthetic solver measurements. Background load affects absolute timing; they do not measure whole-app FPS, GPU, battery or semantic quality. Raw worker and main receipts remain under `outputs/worker-authority-20261018/`, `outputs/pin-geometry-fix-20261018/` and `outputs/runtime-boundaries-20261018/`.

## Verification and delivery

UI 971 tests, desktop Rust 146 tests and strict Clippy passed. [Exact-source CI 39136286507](https://github.com/twoimo/alden/actions/runs/39136286507) passed all four jobs. Candidate and installed focused workspace protocols each produced 34 captures with successful final settling checks. The two selected installed views, eight zoom views and three document views were directly reviewed.

Installed document input answered 53, retained 53 in a follow-up, then answered 67 from a new CSV. Earlier 0.3.148 native tests also exercised images and links. Controlled late-response/cancellation checks used actual model generations before holding the commit boundary. These are bounded fixtures, not exhaustive media or concurrency coverage. The requested default Gemini Flash/high/manual route remained selected; no local model was loaded or substituted for these tests.

All 77 installed files/modes matched the candidate. Strict deep signature, unchanged designated requirement, independent 0.3.148 recovery and protected model/enrollment/emergency settings plus seven collection targets/policies passed. Original source stores and sending queues were preserved.

Local evidence: `outputs/runtime-boundaries-20261018/verification.json`, `installed-visual-review-final.json`, `preservation-installed-final.json`, and `outputs/cross-audit-20261018/completion-manifest.json`. The manifest retains the superseded unfrozen worker-artifact hash separately from the finalized artifact.

The original Gemini adversarial report and final ChatGPT reverse review remain incomplete. The completed ChatGPT thesis predates this implementation and is not certification of it. Physical primary AX/file-picker/microphone/TTS input, independent human semantic/identity/time gold, representative resource/p95 measurements, valid worker activation proof and Developer ID/notarized production delivery remain separate open gates. All 18 goal requirements remain active.
