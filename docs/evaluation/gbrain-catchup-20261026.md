# Explicit verified checkpoints and source catch-up

This is a reversible implementation candidate. The complete Alden goal and existing external/release holds remain active. It does not install a service or change the default reader/watcher policy.

## Caller contract

`FullGbrainCache.stage_existing(generation, ...)` re-verifies an owned, closed, unpublished `STABLE_POLICY` generation. It reconstructs every canonical index row from the source archive, verifies every engine page/revision/outgoing set, checks fresh source authority and complete visibility, closes/reaps the engine, and saves a private `closed-verified.json` plus `pending.json`. It does not trust an old operation/as-of receipt. The source/index/runtime/owner/project bindings and complete integer verification counts are checked when reopening the checkpoint.

The `check` callback returns no value and raises to stop the operation. It must not return a source stamp or another diagnostic object; SQLite progress callbacks also use it.

```sh
python3 -B scripts/alden_gbrain_import.py stage \
  --root /absolute/private-cache --state-root /absolute/declared-source \
  --project one --generation g0123456789abcdef0123456789abcdef

python3 -B scripts/alden_gbrain_import.py refresh \
  --root /absolute/private-cache --state-root /absolute/declared-source \
  --project one --incremental --catch-up
```

The explicit stage operation returns `state: staged`, `published: false`, and `current_source_verified: false`. An opt-in catch-up whose source advances can retain a fully verified closed checkpoint and return `state: pending` with those same false grant fields. A later quiet source may pass every existing source-current publication guard and return `state: published`. Neither pending nor staged is a normal reader access grant. Default refresh/query/watcher never adopts a pending checkpoint.

The incremental source exporter still requires a stable checkpoint throughout its work. If it terminally rejects a content advance and removes its unfinished archive, opt-in catch-up may make a complete canonical as-of export under the same owned generation and lease. Fresh authority, complete visibility and an independently observed content advance are required first; runtime and source binding are checked again afterward. A leftover archive rejects rather than being overwritten or adopted. The fallback precedes engine operations, uses the same declared read-only source, and records `source_update: {mode: full_export, reason: catch_up_source_advanced}`. Non-advance errors and ordinary refresh do not enter it. If neither exporter finishes, only a previously verified checkpoint can survive. Publication and read currentness predicates remain unchanged.

Invalid or partial data never creates a verified checkpoint. Source withdrawal, per-record withholding, changed authority/file/stream, cancellation and runtime integrity failures reject. Source visibility/authority is checked again after persisting pending state. An unreaped child retains its inherited lease and store for recovery, taking precedence over deletion. If cleanup fails, `gbrain_cache_cleanup_required` keeps the original rejection as its cause. Delta still retires its old base after copying, so this is not a rollback API.

Ordinary and private import clients now share the transport's reap check. The legacy cache recovery exception remains a compatible alias. Alive or unknown child status cannot be downgraded to an ordinary error that would allow premature retirement.

## Local evidence

- Changed-boundary tests: 319 methods pass, including 36 independent source-export fallback cases and 193 notice protocol/publication boundary cases. The independent suite's two old-receipt cases require fresh complete re-verification rather than treating an old receipt as proof; staged and pending operation states are distinct.
- Main reproduced four post-persistence withdrawal cases before the fix: stage/refresh crossed with per-record withholding/access-stream replacement. All four failed before the source postcheck and pass after it. Cleanup-failure and actual old-shutdown-body controls verify the relevant rejection/recovery behavior.
- Affected source/collection/MCP/retrieval/realtime tests: 803 methods in 27 modules pass, including those 319 boundary methods. The initial driver fixture errors, missing/misspelled import-path invocation and over-specific test oracles are excluded from accepted results.
- Native GBrain v0.60.451.0 synthetic trial: four pages/five links. An actual source change produces pending and normal-reader refusal. The next quiet-source cycle performs an engine delta with one page/one link replacement, publishes through strict checks and returns an exactly source-bound query. Explicit re-verification of that closed fixture followed by a zero-write delta also publishes. Fifteen owned child processes reaped; cache withdrawn. The fixture's manual demotion of ready/active files is test setup, not a supported way to manufacture an access grant.
- A second native synthetic trial changes the source during the real incremental export's complete comparison. The exporter rejects; a complete canonical export then supplies the engine delta, strict publication and source-bound query. It verifies the full-export fallback reason, repeats closed staging/zero-write publication, and reaps all fifteen owned children before withdrawal. No original operational source is used in either fixture.

At product `1760e015218d2359eb9f769c37a596bd7d268f8a`, exact-head CI passed all four jobs. Actual original five-project staging independently reverified 26,694 pages and 75,006 links, including the 9,436-link paged outgoing set. A subsequent incremental export rejected the live collector's advance and retained the same old verified checkpoint; no new engine delta or latest-source publication occurred in that attempt. This observed lack of progress motivated the complete-export fallback above. A pre-engine driver callback mismatch was excluded: the driver returned a dictionary where the check contract requires no value, interrupting SQLite. Its exact closed pre-stage backup was restored under the same owner and generation identity, then the corrected public API stage completed. No uncertain engine write was replayed and no original source, collector, application or configuration was changed.

The native trial establishes a quiet-source publication witness, not eventual publication under perpetual writers. Full exports, visibility checks, scratch-index reconstruction, closed-store copy/hash and complete readback remain; no O(delta), new latency, memory, token or quota benefit is claimed. Real original-source application, installed UI/account/host, formal/external acceptance and release are separate gates.

Actual read-only diagnostics are inconclusive for an activity-only false-stale hypothesis: two snapshots matched initially, but a later snapshot after cursor advancement changed canonical node rows. The strict publication/read predicates were preserved. Private source material remains local.

The user authorized native Astra only where necessary. Two difficult persistent-authority/recovery implementation and adversarial tasks used native `gpt-6-astra`; routine integration and checks remained with the main model. Native review does not replace the required web-Astra or ChatGPT/Gemini/reverse stages. Fast's saved global tier was already `default`; the active per-thread override is unverified because the exposed control schema provides no setter/read method. No model, reasoning, route, permission, application or service was changed to infer Fast state.

## Passive behavior disclosure

A copied older engine can return `behavior_changes/safety` alongside a committed write. The pinned publisher defines it as disclosure, never consent. The same code can instead carry a source-claim or chat-routing action, so accepting the code alone would widen the interpretation incorrectly. The adapter recognizes only the observed read-only doctor descriptor: exact field sets, strict integer contract version 1, typed flags, bounded rendered WHY and complete metadata/block equality. It records `behavior_changes` in notice metadata, executes no fix and changes no scope, route, capacity or notice setting. Other variants remain unreviewed and reject.

The publisher sources were byte-compared with commit `33d73cb54c14e2e988272764ddff59a5ead10915`: [disclosure constructor](https://github.com/garrytan/gbrain/blob/33d73cb54c14e2e988272764ddff59a5ead10915/src/core/behavior-change-notice.ts#L1294), [source-claim variant](https://github.com/garrytan/gbrain/blob/33d73cb54c14e2e988272764ddff59a5ead10915/src/core/behavior-change-notice.ts#L1323). No suggested operation was executed.

The independent 193 cases cover 42 authority/shape variants and 14 wire/frame variants across three consumers, passive/legacy handling, terminal mutation without retry, and synthetic publication/currentness/withdrawal boundaries. A disposable copy of the historical actual engine accepted a NEW synthetic two-page put with the observed disclosure, added/removed one declared synthetic link, and verified no outgoing link remained. Its one child reaped, probe removed, historical base stayed unpublished, original collection SQL reads were zero, and the failed actual unlink was never replayed. An earlier diagnostic used an undeclared relation verb and was rejected/removed; that incomplete cycle is excluded.

The `7c60e5b` actual catch-up attempt stopped on this notice at its first unlink; all four children reaped and the failed derived store was completely retired. Exact historical bytes were restored under the same owner/generation for independent re-verification. That failed attempt is not a new actual checkpoint or publication. A fresh post-CI application of this adapter remains a separate acceptance step.
