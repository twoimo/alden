# Node details as an in-app popup

Selecting a knowledge node previously split the graph workspace and opened a fixed right reader. The reader now opens as a centered native HTML dialog over the full-width graph. This implements goal ST12; it does not change source records, graph identities, permissions or retrieval authority.

The dialog retains the existing body, linked-note controls and source/version evidence. Its close button, Escape/native cancel and backdrop all use the existing controller clear action, preserving the asynchronous selection fence. A pointer drag from the content to the backdrop does not close it. Closing returns focus to the graph canvas. A new selection resets reading position; a same-node asynchronous detail update preserves scroll, disclosure and focus. Hidden workspaces and disposed graphs cannot open the reader.

The reader has a sticky heading and close control, internal scrolling, bounded viewport margins and narrow-window wrapping. No graph split, blur or continuous popup animation is added.

## Verification

- Full UI suite: 91 files, 1,025 tests passed. Includes popup behavior, hidden-workspace suppression, cancellation/late-result integration and displaced-label picking regression.
- Hidden IAB render uses production `settingsMarkup`, `KnowledgeHologram`, `renderNodeDetails`, visibility helper and styles, with a read-only snapshot of 1,984 actual installed graph nodes and two actual source detail payloads (12,000 and 7,853 characters). A stale initial fixture correctly returned `collection_graph_version_changed`; refreshing the overview restored version-consistent details. No stale source body was accepted or fabricated.
- Browser input verified Escape, close, focus return, native keyboard containment, outside click, drag-out retention and a 900px internal body scroll with a stationary heading. Direct visual review covered 1440×900, 1200×760, 640×680 and 480×560; no dialog horizontal overflow. The last size uses emulated DPR2, not physical Retina evidence. Browser error log was empty.
- Evidence: `outputs/node-popup-20261020/ui-tests-final.log`, `browser-checks.json` and four directly reviewed PNGs. Private source snapshots stay untracked and localhost-only. Owned QA tab/server were closed; original research tabs were preserved.
- Delivery target is 0.3.198. Build, installed file/signature/configuration readback, running bundle and CI results are recorded separately in the delivery manifest and task-root progress files. A hidden browser render does not prove installed native mouse/keyboard behavior; no foreground native audit is run because the user requested background QA.

## Independent database experiment

Two bounded text-cache batch candidates were tested against frozen read-only source/index transactions. Returned rows and order matched, but time-filtered queries regressed in both OR and VALUES/CROSS JOIN trials. Both candidates were rejected; the retrieval script and its test file were restored exactly to their existing HEAD contents. The production retrieval SHA-256 remains `5386aa19a49940e512b1e4c5713adf1f5a05b188af9b1f5844b47da4badf70ca`. Reduced SQL statement counts alone are not a performance acceptance criterion. Candidate evidence and `decision.json` remain under `outputs/retrieval-text-batch-20261020/`; no database migration or native scoring promotion is included.

The full 18-item objective and ST01–12 remain active. Database cross-audit, representative whole-app performance, independent semantic gold and physical/native/release gates remain separate.
