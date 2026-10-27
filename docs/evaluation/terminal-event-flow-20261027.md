# f(x) stored document transitions — 2026-10-27

The terminal previously counted the displayed nodes' `updatedAt` values and selected the last14 distinct modification dates. One node could produce an apparent intake bar before any collection history had been read. The user requested actual intake/change by period; node modification dates do not establish that quantity.

The existing `collection-history` action now optionally aggregates the retained, currently permitted journal in the same read transaction as its history and checkpoint. `f(x)` displays confirmed stored-document transitions, independently of the visible graph and the recent80-row history page. It remains a supported subset of the requested full source activity.

## Scope and unit

The request adds `trend: {since, until}` in finite Unix seconds, with `0 <= since < until` and at most31elapsed days. It must fit any selected time filters. A supplied stage must be `stored`; other stage+trend combinations reject with `collection_trend_stage_invalid`, while ordinary history stage filters remain supported. The default is the current UTC calendar day and preceding13days; explicit local-date endpoints from the existing caller are preserved. All intersecting UTC dates are returned, including partial/empty dates. Invalid or unsupported ranges are shown as unaggregated, without silent clipping.

Selected projects, target, platform, history search and current source permission guards remain effective. Each completed-run stored `(run,target,document,version,change)` group is one transition, assigned to its earliest retained stored receipt. Only explicit `added`, `revised` and `removed` count. A deferred receipt also needs its existing published-readiness evidence. Intermediate stages and `unchanged` do not inflate counts. Unknown/missing/unfinished/unpublished groups are excluded and counted separately as unclassified.

The aggregate binds `snapshot_checkpoint.stream_id/cursor`; top-level `cursor` remains history-page progress and may be lower on truncated forward pages. The frontend validates the exact interval/date sequence, safe nonnegative counts, combined classified/unclassified upper bound, current stream/checkpoint, completion and observation time. It discards malformed, partial, stale and mismatched responses. New scopes can proceed while an old request is unresolved; epoch ownership prevents late success/error from taking over. Follow keeps history and aggregate snapshots paired; pause is explicitly labeled. No renderer, camera, timer or subscription is added.

## Coverage limits

`basis=stored_receipt_utc`, `scan=complete`, `coverage=retained_only`, `source_coverage=unknown`, `backfill_live=unknown`, and `relations=null` are explicit response fields. A complete scan proves the retained journal query, not source acquisition or retention completeness. Empty buckets mean no confirmed retained visible receipts; they do not prove zero actual source activity. Historical batches are dated when stored. Source occurrence time, full original intake, relationship volume and backfill/live separation are not inferred. Conversation-memory and type/relation-only graph trends remain unsupported.

31days bounds output, not SQL work: canonical deduplication occurs before the time predicate, so the query may scan retained scoped stored receipts and publication evidence. Existing read cancellation and post-read authority checks remain; no new scanned-row/time bound or materialized aggregate is introduced. Runtime throughput, scalability and resource improvement are unmeasured.

## Verification and delivery

- A DOM negative control on copied old source fails because a displayed `updatedAt` creates a false intake bar without history.
- New isolated backend regressions16/16 and existing history/activity/permission cases9/9 pass. They cover multiple-page histories, canonical duplicates/out-of-order timestamps, no-change/stages, publication, deletion, permission filters, exact dates and pruning/reset.
- Frontend tests cover104unique cases:103passed across five files, then the final numeric-bound parser suite40/40passed (39overlap). Cases include malformed/partial evidence, interval gaps/order, impossible counts, stream/page checkpoint separation, scope switch, late error, pause/follow and disposal. Parent full desktop TypeScript and isolated production Vite build pass after the final source hashes were independently reread.
- Requested native worker model/effort was `gpt-6-astra/ultra`; actual metadata was not exposed. Test runtime, logs, per-file before/after hashes and the meaningful negative control are retained in task evidence outside the repository, `outputs/terminal-event-flow-20261027`.

Supplemental user-authorized OpenCodex Argon/high reviewed the scoped literal code and synthetic tests, with substantive source anchors and no actionable P1/P2. Its noncritical stage-filter observation was reproduced (one method, seven subtest failures) and fixed by rejecting incompatible stage+trend inputs. The fixed backend suite passes16/16, including ordinary stage-only history and accepted stored-stage controls. This static feedback executed no tests/original data; the response completed200 with model echo `gemini-4-argon`. Canonical upstream/provider log proof remains unverified after the earlier401; no retry or bypass was used.

At this source checkpoint, changed-source CI, genuine original-journal reconciliation and rendered/native acceptance remain separate subsequent gates. Existing af36graph-browser evidence does not validate this new terminal path. Alden remains OFF; no install, schema migration, model/service/global settings/capacity change or private-data upload is part of this change. Required formal web-Astra/Gemini/reverse review and release remain incomplete.
