# Explicit verified checkpoints and source catch-up

This is a reversible implementation candidate. The complete Alden goal and existing external/release holds remain active. It does not install a service or change the default reader/watcher policy.

## Caller contract

`FullGbrainCache.stage_existing(generation, ...)` re-verifies an owned, closed, unpublished `STABLE_POLICY` generation. It reconstructs every canonical index row from the source archive, verifies every engine page/revision/outgoing set, checks fresh source authority and complete visibility, closes/reaps the engine, and saves a private `closed-verified.json` plus `pending.json`. It does not trust an old operation/as-of receipt. The source/index/runtime/owner/project bindings and complete integer verification counts are checked when reopening the checkpoint.

```sh
python3 -B scripts/alden_gbrain_import.py stage \
  --root /absolute/private-cache --state-root /absolute/declared-source \
  --project one --generation g0123456789abcdef0123456789abcdef

python3 -B scripts/alden_gbrain_import.py refresh \
  --root /absolute/private-cache --state-root /absolute/declared-source \
  --project one --incremental --catch-up
```

The explicit stage operation returns `state: staged`, `published: false`, and `current_source_verified: false`. An opt-in catch-up whose source advances can retain a fully verified closed checkpoint and return `state: pending` with those same false grant fields. A later quiet source may pass every existing source-current publication guard and return `state: published`. Neither pending nor staged is a normal reader access grant. Default refresh/query/watcher never adopts a pending checkpoint.

Invalid or partial data never creates a verified checkpoint. Source withdrawal, per-record withholding, changed authority/file/stream, cancellation and runtime integrity failures reject. Source visibility/authority is checked again after persisting pending state. An unreaped child retains its inherited lease and store for recovery, taking precedence over deletion. If cleanup fails, `gbrain_cache_cleanup_required` keeps the original rejection as its cause. Delta still retires its old base after copying, so this is not a rollback API.

Ordinary and private import clients now share the transport's reap check. The legacy cache recovery exception remains a compatible alias. Alive or unknown child status cannot be downgraded to an ordinary error that would allow premature retirement.

## Local evidence

- Changed-boundary tests: 90 methods pass. The independent suite's two old-receipt cases require fresh complete re-verification rather than treating an old receipt as proof; staged and pending operation states are distinct.
- Main reproduced four post-persistence withdrawal cases before the fix: stage/refresh crossed with per-record withholding/access-stream replacement. All four failed before the source postcheck and pass after it. Cleanup-failure and actual old-shutdown-body controls verify the relevant rejection/recovery behavior.
- Affected source/collection/MCP/retrieval/realtime tests: 574 methods pass. The initial driver fixture errors and over-specific test oracles are excluded from accepted results.
- Native GBrain v0.60.451.0 synthetic trial: four pages/five links. An actual source change produces pending and normal-reader refusal. The next quiet-source cycle performs an engine delta with one page/one link replacement, publishes through strict checks and returns an exactly source-bound query. Explicit re-verification of that closed fixture followed by a zero-write delta also publishes. Fifteen owned child processes reaped; cache withdrawn. The fixture's manual demotion of ready/active files is test setup, not a supported way to manufacture an access grant.

The native trial establishes a quiet-source publication witness, not eventual publication under perpetual writers. Full exports, visibility checks, scratch-index reconstruction, closed-store copy/hash and complete readback remain; no O(delta), new latency, memory, token or quota benefit is claimed. Real original-source application, installed UI/account/host, formal/external acceptance and release are separate gates.

Actual read-only diagnostics are inconclusive for an activity-only false-stale hypothesis: two snapshots matched initially, but a later snapshot after cursor advancement changed canonical node rows. The strict publication/read predicates were preserved. Private source material remains local.

The user authorized native Astra only where necessary. Two difficult persistent-authority/recovery implementation and adversarial tasks used native `gpt-6-astra`; routine integration and checks remained with the main model. Native review does not replace the required web-Astra or ChatGPT/Gemini/reverse stages. Fast's saved global tier was already `default`; the active per-thread override is unverified because the exposed control schema provides no setter/read method. No model, reasoning, route, permission, application or service was changed to infer Fast state.
