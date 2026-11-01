# Graph worker transport failure recovery — 2026-10-27

Worker creation errors previously escaped the graph request. A synchronous `postMessage` error also left the driver busy, causing an identical explicit request to be ignored. A runtime worker error retained its transport and callbacks; a queued completion could replace the failed state with ready before a retry.

`SemanticLayoutDriver` now contains creation/post exceptions and retires the current transport on runtime errors. Failure sets the fixed `semantic_layout_worker_failed` reason and an allowlisted `failureStage` (`create`, `post`, `runtime`). Retirement invalidates the generation, clears pending ownership, detaches handlers and terminates the worker. Saved callbacks cannot apply a completion or retire a newer generation. Exception text is discarded.

There is no automatic retry or main-thread solver fallback. A later explicit request that needs a solve creates a fresh worker; a legitimate completed cache can still satisfy its unchanged authority-bound view. `stop()` keeps the last completed checkpoint and `clear()` removes it, preserving the existing cache contract. Successful request contents and layout calculations are unchanged.

## Verification

- A new independent native gpt-6-astra suite uses mocked transport with the real driver, parser and layout engine: 32/32 pass. Copied `6401f55` baseline: 1 pass/31 failures. It covers default missing/denied Worker construction, post errors, runtime faults, repeated explicit retries, stale callbacks, supersession, completed cache, authority changes, stop/clear and direct engine result equality through pin/recovered reflow. Actual worker reasoning effort was not exposed.
- Existing 11 layout/driver/worker/contact/center/helper suites: 257/257 pass. Full desktop TypeScript no-emit check and isolated production Vite build pass. Existing test files remain unchanged.
- Supplemental OpenCodex Argon high review found no actionable P1/P2 in the supplied driver delta and existing tests, with substantive source readback. It did not execute tests or inspect original data. The local response completed200 with model echo `gemini-4-argon`; management log readback returned401, so independent canonical route/upstream proof is not claimed. No auth/settings change or retry bypass was used.

This establishes source and controlled transport behavior, not actual browser Worker scheduling, screenshots, GPU/FPS, native installation/TCC or release acceptance. Incoming `messageerror` deserialization coverage and ordinary application-error replies are outside this transport delta. Native Alden stays OFF; pending browser/model questions and formal external audit/release holds remain. Task evidence: `outputs/graph-worker-failure-recovery-20261027` outside the repository.
