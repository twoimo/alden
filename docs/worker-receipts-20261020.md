# Private reply receipts and worker regression repair — 2026-10-20

Baseline: `9c115f9243086c1b26a938a5bccb801ffc0771c4` / Alden 0.3.193. Installed: 0.3.194 from runtime source `6aa823c4b75344588994828df06ec23c9e4493e6`; subsequent test/CI/documentation changes do not change that installed worker hash. No live send is part of this verification. Signed app, installed readback, immutable worker candidate and worker activation are separate delivery gates.

## Reproduced product fault

The first evidence record could create the default operator-state leaf with mode0755. The next turn's real abort-state reader then classified that non-private directory as `state_error` and cancelled the request. A controlled two-turn model-deferral fixture demonstrated the first receipt file becoming0600 while its parent stayed0755; the second turn deferred with `alden_global_abort`. This was an initialization defect, not permission to weaken the abort reader or clear its latch.

Both evidence and delivery records now use `_append_private_reply_receipt` in `scripts/auto-reply-worker.py`. It creates a new leaf0700, rejects a pre-existing shared leaf without changing its mode, and uses the existing owner/link/inode-checked `_private_lock` boundary before appending a UTF-8 JSON line. The file is private before writing; cooperating writers serialize through flock. Short writes are completed and the descriptor is fsynced. Symlink/hardlink targets retain their original bytes and permissions.

Stress verification also reproduced a first-creation openat ENOENT while the owned directory remained linked and the receipt entry existed:16 failures across30 isolated concurrency trials. The append operation re-enters all file checks at most twice only if the lock context has not admitted the writer. It never replays a write/fsync outcome. This describes the observed failure and bounded repair; it does not establish an OS/filesystem root cause.

## Verification and its limits

- Entire worker module plus abort suite:391 tests passed after the final guard repair.
- Fresh-process affinity/layout/sampling/JSON:86 tests passed without changing production RSS/time/CPU limits.
- Fixed concurrency/partial-write cases:52 passed, including50 concurrency trials (24 receipts / four writers per trial), an injected pre-write creation failure and write/fsync uncertainty with no duplicate replay.
- New boundary regressions cover first receipt→second abort token, shared-parent refusal, symlink/hardlink refusal, complete concurrent JSON lines, partial writes, zero progress and post-write failure.
- Full general discovery ran2,900 tests with28 skips and no failures/errors, separately from the86 memory-budget cases. The first broad harness used stdin, which broke multiprocessing spawn; its failed receipts are retained. The final file-based main runner and discovery/result manifests establish the counts.
- After CI exposed host dependencies, the complete worker module passed371 tests with portable fixtures. Metadata-contract tests declare owned safe/unsafe interpreter files; payload tests separately execute the actual Python with explicit `-E -B -S` argv. They do not assert that a group-writable CI interpreter passes the production fence. Retrieval fixtures provide their own never-executed CLI file, and policy-branch fixtures explicitly supply their synthetic durable-projection result.

The actual worker default model, routed/local model admission, permission/identity/freshness checks, queue transitions and emergency policy are unchanged. Valid supervisor proof and preflight still gate activation. No existing queue receipt is cleared or relabeled, and no old worker/model is restarted by these tests.

## Why the old worker tests failed

The isolated baseline worker run had32 failures and23 errors across366 tests. Several tests omitted current contracts: producer-owned image provenance, a private existing decision projection, a room/epoch/owner-bound watcher file, a scheduled draft's analysis watermark, explicit vision capability, the asynchronous context-sync result and the processing/sending transition at the intended race point.

Fixtures now provide these inputs explicitly and exercise the real validators. Malformed identity cases retain rejection coverage. Image-order verification compares the admitted vision payload's decoded bytes in source order; a separate test proves that an unregistered CLI model receives neither private text nor images. Failed media keeps its original bundle while durable path capabilities are scrubbed. Timing tests retain the fitted mixture and anchor/remaining distinction; laughter tests check permitted resampling instead of assuming one random length.

Four legacy text-CLI component fixtures explicitly stub admission for their one pinned primary solely to test fake executable/protocol behavior. They do not prove that the product admits those old cloud IDs. The fake vision gateway advertises its tested capability explicitly, all process/model results remain synthetic, and actual external model generation is not exercised.

CI now runs the complete worker module in its own process and includes sampling/JSON with the existing fresh-process affinity tests. This keeps RSS admission checks meaningful and prevents the former single focused worker method from standing in for the full module. Run39418248980 exposed two failures and six errors caused by the test host assumptions above; its failure is retained, not reported as a pass. The corrected revision requires its own CI receipt.

## Delivery gates still separate

The app bundle and the Terminal-hosted immutable worker runtime are different artifacts. Installed194 matched78 candidate files by SHA/mode and passed strict/deep fixed-signature verification. Model/enrollment/emergency settings and seven targets' configurations were preserved; source/history counts were unchanged except12 events added while the existing collection watcher continued. No watcher or model restart was needed.

An isolated immutable candidate matched56 assets by SHA/mode, including worker SHA `66f4063715df8892f9b80c9240627490eef9261fde43e6d05316149e587cb0af`. Its pinned `-B` execution passed first-receipt→next-abort-token verification and did not create bytecode. It was not activated and did not modify the production stable CLI or queues. In the current host implementation, `auto-reply-host --bake` requires a source checkout; app installation alone does not prove a worker rollout. Standalone packaging and normal worker activation remain full-goal gates until their own evidence exists.

The DB cross-audit must carry this storage-boundary delta along with the current source revision. The local fix/tests do not complete the pending ChatGPT→Gemini→ChatGPT review or validate a DB-engine migration.
