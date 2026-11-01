---
name: verify-alden-realtime-state
description: Verify Alden watcher retry, gate ordering and pending-input preservation after changes to realtime collection, scheduler ownership or SQLite error handling.
---

# Verify Alden realtime state

Read `docs/evaluation/messenger-stream-20261021/STATE_VERIFICATION.md` from the
repository root. The property is that transient SQLite contention preserves
durable work and returns to a fresh gate check after a bounded wait; non-contention
errors stay visible. This protects against silently losing the realtime worker
or collecting when current source/control state is unknown.

Coverage: `scripts/alden_realtime_collection.py:watch`, its calls to
`CollectionStore.open_existing`, scheduler pause/abort/voice/cycle and realtime
policy/scan/heartbeat. Tests are `tests/test_alden_realtime_collection.py` and
`tests/test_alden_realtime_state.py`. This does not verify the scheduler's full
publication protocol, source arrival, native readers, UI/combat or installed app.

Baseline inspected: `1eb223652533fb47c66a8e888716c1088c134569`.
Aligned implementation: `04b854b9600046d6b7549a038de58b026ed6e163`;
watcher SHA-256 `3bdda372a926c64fe1394371d746e4aeb690a827d801ce341ce61cf235a59043`.
Current source alignment is additionally bound by the SHA-256 in the generated
verification receipt. Update this record to the delivered source commit only
after confirming that the modeled boundaries still match that implementation.

Run `python3 -B scripts/verify_alden_realtime_state.py` from the repository root.
Use the pinned menu Python 3.11 runtime for local delivery verification. The
checker runs 84 generated busy/control traces, two periodic retry cases, eight
fatal-error cases (two ticks maximum), a real private SQLite-lock journey and
two temporary mutations of actual source. Current code must pass and both
broken variants must fail. No product source, native DB, consent, model, queue
or installed Resources is changed; temporary variants are removed automatically.

If a mutation is not rejected, inspect source-to-property alignment rather than
weakening the property. If an intended source change makes a mutation anchor
obsolete, review the new transition, update the discriminating broken variant
and rerun. Preserve a genuine counterexample. Source changes, bound changes and
test-oracle errors are distinct findings. This is bounded generated-input
verification, not an unbounded mathematical proof or whole-project certification.
