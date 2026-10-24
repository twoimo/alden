# Reject conflicting input before storing attachments

The desktop assistant rejected a reused request ID carrying different input, but stored its new image/document bytes before detecting the conflict. A rejected retry could therefore leave an unreferenced blob even though its original conversation and answer remained unchanged. The same problem occurred for an existing pending request whose original process no longer held the input lock.

Alden 0.3.209 checks the existing durable request's payload and originally requested conversation while holding the existing request lock, before persisting image or document bytes. A known conflict returns the existing `request_id_payload_or_target_conflict` failure. The authoritative reservation/commit checks remain in place; a matching pending request still follows the existing context preparation and cancellation fences. A matching completed replay retains its original receipt and does not reparse or infer.

This reuses the read-only replay inspection and does not introduce a new identity, schema, model, context policy or writer. It covers conflicts already present when this cooperative input owner obtains the request lock. It does not make unrelated raw database writers participate in that lock, remove historical orphan files, or change storage behavior for other failure paths.

## Verification

Two behavioral regressions reproduce three failures before the fix: different document and image bytes on a completed ID, and different document bytes on a pending ID. Each request already reported a conflict, but the stored blob set grew. The corrected checks require exactly the original blob bytes and durable request, without another generation call.

An additional malformed-image-metadata regression preserves the existing failure receipt when preflight validation rejects an input, before storing any bytes. The first guard revision raised an exception for that case; its failure evidence is retained.

All 96 affected assistant input, document, image-context, turn, link and generation-store tests pass after correction. They retain real parser-child tests, same-conversation document follow-ups, completed replay, revocation/corruption/cancellation/supersession fences and provenance checks. Model outputs and model selection in these tests are controlled fixtures; no live model quality or successful provider inference is claimed.

The same new regressions are run against the actual installed 0.3.208 modules before delivery. Staged and installed 0.3.209 checks, signatures, exact bundle hashes, preserved configuration/target/scheduler settings and remote CI require their own receipts under `outputs/attachment-admission209-20261021/`. Source tests alone do not establish installation or native UI behavior. No foreground QA, live message, microphone session, retrieval candidate promotion or source-graph mutation is needed for this correction.

The current collection recovery evidence is retained in [the recovery report](collection-recovery-20261021.md). The full objective and its independent semantic, external review, physical interaction, model evaluation and normal release gates remain open.
