# Whole-database messenger collection candidate

Status: reversible isolated evaluation, not installed or externally validated.
Base: `1a6e0fd5e78e5f1462a6a2ce59af059ebf9de0b3` (installed Alden 0.3.214).

## Intended outcome and scope

The user requested **all KakaoTalk and Telegram databases**, with unnecessary
content, duplicates and outliers filtered so useful knowledge can grow. Existing
18 requirements, ST01–13 and Cursor-session gates remain in force. The requested
scope is not the previous three Kakao enrollment rooms. Actual local cache
coverage is distinct from complete server history. Source visibility, current
account, consent, and read-only guards remain binding.

ST14 acceptance requires all-room discovery and fair incremental traversal,
independent history/live checkpoints, reversible quality classification, exact
content deduplication with every origin retained, safe edits/withdrawals/expiry,
and installed collection → index → retrieval/provenance → graph → real combat
verification. Backfill must be distinguished from newly arriving source content.
No unchanged replay, idle timer or denied source may generate fake activity.

## Decisions and sequence (externally unvalidated)

| ID | Candidate decision / alternative | Evidence and acceptance |
| --- | --- | --- |
| M01 | One frontier per pinned account/platform, per-room checkpoints; alternative: hundreds of independently scheduled targets | Existing scheduler has bounded target scans. Enumerate every cached room, no silent 200-room cutoff; isolate failed rooms and drain multi-page bursts without losing history. |
| M02 | Durable prepared pages bridge frontier/store commits; alternative: one cross-domain transaction | Existing store owns version/FTS/activity transactions. Kill after publication, resume exact bytes, prove no second version/event or lost checkpoint. Pending visibility/permission changes must fail closed. |
| M03 | Preserve raw authorized pages and reversible context/review/candidate states; alternative: discard low-score messages | Short acknowledgements/numbers can be important context. Context assembly and reclassification must recover them. Independent human gold is required before semantic-quality claims. |
| M04 | NFC exact text content identity plus separate original identities; alternative: semantic/entity merging at ingestion | Keep numbers, direction, case and internal whitespace. Preserve every room/message origin. One origin withdrawal must not remove another available membership. Similarity alone never establishes identity or a factual relation. |
| M05 | Reconcile source authorization and current visibility before continued exposure | Consent expiry/date-scope changes and old prepared pages are production gates. Withholding must remove current projection while retaining recovery/provenance; renewal requires current canonical source validation. |
| M06 | Use the existing real activity journal for bounded visual effects | Measure source commit → durable publication → scene refresh → receipt consumption. No fabricated relation, event or recent timestamp. Verify idle, cancellation, hidden/minimized and reduced-motion behavior. |

Work order: source inventory/boundaries → durable frontier and quality candidate →
authorization/visibility reconciliation → conversation context and provenance →
production scheduler integration → background installed journey/resource checks →
delivery. Consequential promotion also requires the actual canonical cross-audit:
ChatGPT Pro thesis → Gemini maximum-thinking adversarial review with scoped code →
ChatGPT Pro reverse verification, ordinary reasoning with research off. Existing
uncertain/running research and other owners' reservations must be preserved.

## Observed evidence, not production completion

Private evidence is in `outputs/realtime-battle-20261021/`, excluded from Git and
external uploads. Canonical readers found 370 cached Kakao rooms and 337 supported
Telegram conversation peers. The first isolated pilot processed 2,514 Kakao
message heads: 564 candidates, 1,943 context, 1 review and 6 withheld. Exact
candidate text produced 540 documents with the additional 24 origins retained.
Only 19/370 rooms reached their backfill boundary. All 337 Telegram rooms had no
currently valid body context at pilot time, so no Telegram body request ran.

First-pilot candidate SHA-256:
`a18b7eedc4d9badc065a07aa3a1945e118257f81af72852ee700dc8f5d12f60b`.
Its exact bytes are privately retained; later candidate changes do not inherit
that pilot's runtime evidence. Counts do not establish semantic correctness,
source completeness, live latency, installed behavior, or resource improvement.

`messenger_quality.py` contains deterministic triage, not a truth classifier.
`messenger_candidate.py` only accepts an explicit marked private evaluation root.
Production has no messenger watcher/targets from this work. No source DB writes,
messages, consent-registry changes, model substitution or service restarts occur.

## 2026-10-23 scoped corrections and verification

The prepared-page test reproduced publication of an old Telegram date scope
under a newly authorized, different date scope. The candidate now copies the
authorized bounds before reading and compares them again before publication.
It returns `telegram_prepared_scope_changed`, retaining the pending job for
explicit reconciliation. This prevents that publication; it does **not** retire
already published content or automatically rebase old date cursors.

Independent `reconcile` traversal now revisits old cached rows without moving
the live discovery head or backfill frontier. Explicitly withheld rows retract
their current membership; absent rows alone are not proof of deletion. A second
regression caught completed-page replay leaving this cursor stuck at the first
page. The completed-job path now applies the prepared next cursor without
republishing versions/events. Missing cached rows, expiry and changed scopes
remain separate production gates.

The first-line label regression reproduced a bare `1` graph label despite useful
content below it. Candidate policy `messenger-triage-nfc-v2` reuses the store's
existing substantive display-label extraction. Body, exact-content identity and
the complete original page remain retained. Old pending quality-policy versions
are rejected rather than silently reinterpreted.

An archived-input replay also found URL length inflating short/decorative
messages into candidates. Candidate eligibility and labels now use the text
outside URLs; the complete URL/body and content hash remain intact. A substantive
same-line caption remains eligible and provides its label. Link content must be
acquired and validated separately before it can supply knowledge.

| Journey | Control / oracle | Evidence level and result |
| --- | --- | --- |
| Date-scope replay | Canonical CLI adapter with controlled responses; changed authorization before prepared publication | Synthetic adapter test: old scope blocked, source body not fetched again, no searchable publication. |
| Old edits / withheld rows | Independent multi-page reconciliation, repeat completed pages | Real isolated SQLite, substituted source: revised text searchable, withdrawn origin removed, live/backfill state preserved, repeat cursor continues and no new event. |
| Labels / raw preservation | Numeric/URL header followed by substantive text | Real isolated store: substantive label, exact retained source/version body. |
| Affected collection boundaries | Pinned menubar Python 3.11 with `-B`, eight affected unittest modules | 195 passed, including 33 frontier checks. Source/fixture evidence, not installed collection. |
| Installed preservation | Read installed collection bytes against base HEAD; strict/deep signature check | Alden 0.3.214 collection script unchanged; signature passes. The candidate is uninstalled. |

Private logs: `scope-reconcile-baseline-20261023.log` (one failure, one unsupported
mode error), `reconcile-repeat-baseline-20261023.log`,
`labels-baseline-20261023.log`, `frontier-final-20261023.log` and
`url-substance-baseline-20261023.log`, `frontier-final-v2-20261023.log`,
`affected-final-v2-20261023.log`. Source hashes and archived-input replay are
separately recorded under the private evidence directory. Archived replay is
not present source visibility, new live acquisition or a performance result.

Final archived replay (`archived-replay-receipt-20261023d.json`) processed all 63
retained pages / 2,514 heads. V2 classified 558 candidate, 1,949 context, 1 review,
6 withheld; 535 exact documents/versions retain 23 additional candidate origins.
The six newly contextualized source rows remain in retained pages, not deleted.
Two full reconciliation cycles on the 19 rooms with complete archived history
kept documents/versions/changed events at 535 and returned no visual activity
through the real store activity API. URL-prefixed labels were zero. This is
structural replay evidence, not independent semantic gold.

All preliminary receipts are retained. The first replay assertion mistakenly
equated legitimate `stored/unchanged` audit receipts with changed activity; the
correct oracle inspects changed kinds and the actual activity API. The next
archive attempt correctly rejected a restarted page in a partially retained
room as stalled; complete cycle coverage is limited to 19 rooms with a known
archive end. The third attempt exposed the URL-substance defect above. None of
these preliminary attempts is recorded as passing or as live source behavior.

Shared cross-audit status was read on 2026-10-23: no eligible account/slot was
admitted; existing other-owner operations remain preserved. All three external
stages for this messenger delta remain incomplete. The previously approved
terminal review keeps its original source/job/target and is not replaced by
this delta. The canonical skill now requires ordinary reasoning, research off;
historical uncertain research is not canceled or relabeled.

## Publication and recovery follow-up

An expiry injected after source-byte storage reproduced a publication after the
source authorizer had become invalid. The candidate now uses the store's
existing commit fence to recheck current account, target permission and source
scope immediately before the product commit. Expiry rolls back versions, FTS,
stored-change events and the target cursor together; the immutable prepared page
remains resumable after authorization returns. This is not an access lease or a
read-time expiry filter for previously published documents.

Prepared recovery also revalidates visible body bytes and source timestamps.
Kakao uses the canonical reader's exact `message_id` query, at most 50 one-row
lookups, so new arrivals cannot move the requested record outside a latest-page
window. Telegram's current CLI has no exact-ID read; recovery rechecks its
original bounded page. An absent record produces
`messenger_prepared_window_incomplete`, never a fabricated deletion. A changed
or withheld record blocks stale publication. Rebase/supersession of changed
pending pages and retirement of already committed stale content remain gates.

An initially empty, fully traversed room now stays eligible for live checks and
can acquire its first arrival. Unvisited rooms retain their backfill prerequisite.

Prepared payload and next-frontier bytes are also rehashed against their job ID
before any source access or publication; a changed checkpoint fails integrity
validation rather than moving the cursor under an old job identity.

Verification: 201 affected Python checks, including 39 frontier checks. Failure
baselines are `commit-guard-baseline-20261023.log`,
`recovery-visibility-baseline-20261023.log`, and `empty-room-baseline-20261023.log`.
The checkpoint-integrity baseline is `prepared-integrity-baseline-20261023.log`.
The actual canonical Kakao reader also validated one retained message by exact
ID against current native SQLCipher/WAL bytes: match, read-only/schema verified,
one source lookup, zero source/server/send/settings writes. That host read is
recorded in `native-exact-recovery-receipt-20261023.json`; it is not a full-history,
installed-watcher, latency or currently expired-access retirement claim.

## Remaining delivery gates

- Authorization expiry/scope rebasing and prepared-page visibility reconciliation.
- Conversation context, review/reclassification and independent semantic/temporal gold.
- Complete source traversal, edit/delete detection and bounded fairness under load.
- Product provenance/MCP/UI integration; no private source data in external audits.
- Scheduler/startup/cancellation/restore, index and graph consistency, actual combat.
- Measured collection latency and CPU/RAM/DB/render budgets on representative load.
- External three-stage review bound to the precise source bundle, then installed
  verification, source/config/data preservation, PR/CI and normal release gates.
