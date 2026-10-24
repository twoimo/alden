---
name: verify-alden-messenger-publication
description: Recheck messenger store/frontier/outbox commit ordering, current-version read authority and durable activity receipts after relevant Alden source changes.
---

# Verify messenger publication

Read `docs/evaluation/messenger-stream-20261021/PUBLICATION_STATE.md` from the
checkout. The property is that reads between separate database commits must
neither expose a body before the frontier commits nor permanently lose its
later refresh receipt. A wrong current membership/version must remain denied.

Source coverage is `Frontier._prepare`, `_publish`, `_source_access`,
`_release_activity`, `step` and `activity_releases` in
`docs/evaluation/messenger-stream-20261021/messenger_candidate.py`, and
`CollectionStore.ingest`, `release_activity`, `_event` and `activity_page` in
`scripts/alden_collection.py`. The UI's history counts and terminal labels keep
the readiness stage distinct from stored changes. Base inspected is
`7d7a5149f8a05dbd913add6cf8e5cc5fa3f4adc6`; implementation aligned at
`583bee9e54aa76721ac62e63fb5585cd0a8c014a`. Exact tested source hashes live in
the verification/native receipts. Advance the sync revision only after checking
that each modeled boundary still corresponds to a real commit in the source.

Run `python3 -B scripts/verify_alden_messenger_publication.py` using the pinned
menu Python3.11 interpreter for local delivery checks. Current source must pass
all15 isolated SQLite/source-boundary cases, and all3 temporary broken variants
must fail. The checker does not write native sources, send messages, grant
consent, launch models or alter installed Resources. It removes its temporary
mutants. Reconfirm the actual controlled trace and retain a genuine counterexample
if the check fails; do not weaken body-withholding or cursor-delivery properties.

These are bounded behavioral/mutation checks. No whole-system mathematical
proof, complete source-visibility guarantee, installed UI/combat verification or
unbounded liveness follows. Runtime/private native probes, external review and
release gates remain separate. Source/claim mismatch is a modeling error;
a real violated invariant remains a product finding.
