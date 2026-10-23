# Messenger adversarial findings and current decisions

The original ordinary Gemini4 Argon maximum-thinking review is now saved and
byte-verified against its actual attached source bundle. It covers source
`2f5f67838ad7902583f0782a1617c11e1fe58c36` against base
`513694a34d8122ab9a3f90050e9cf87d8e16fd90`, not the current implementation.
The full unedited visible-response export retains AX structure; it is not a
vendor Markdown download. Its SHA-256 is
`451a99b472669b6c5a07708e83c463fe0a1139bba893255c5dd39aa2bfb1da96`.
The complete 14 finding sections and final work sequence are preserved in private
`outputs/audit-recovery-20261023/gemini_4_argon_review.md`. That name aliases the
same immutable bytes as the registered export. Full-page observation and export
metadata remain separate from the report.

Three substantive source fragments match the actual attached 839057-byte bundle,
SHA `2ff3c8cd57eb5d629c05459b522b1140b055720d7cdd317b9c9b1fe0beb70c06`:
the authority lookup, permission-bearing source SQL and old stripped classifier.
The review explicitly claims no test execution. Source readability, completed
report, local reproduction, native execution, installation and release remain
different evidence.

## Original operation reconciliation

The coordinator previously supported ChatGPT provisional/home URL redirects,
but rejected the same owned Gemini `/app` → canonical conversation transition.
A guarded metadata correction now requires the exact dispatched owner/original
URL, ordinary mode, matching accepted source/prompt/account/filename identity
and a canonical default-host Gemini conversation URL. It changes neither job
identity, quota/admission nor source bytes and cannot replace a canonical chat.

The positive baseline tests failed before correction. 176 isolated coordinator
checks pass, all 19 installed Python files match that tested candidate, and 6
focused checks pass through the canonical installation. A pre-existing macOS
test fixture used `/var` instead of canonical `/private/var`; its temporary
path was corrected while retaining the actual nonsymlink/private-file guard.
Original failure logs and scoped backups/patches are preserved. No shared state
was edited directly.

Supported owner commands recorded the original accepted/readable source,
canonical redirect, ordinary answering/completion and full artifact. Artifact
bytes were reverified and the completed reservation was released promptly.
No review was resent and no account/model was substituted. Original URLs,
owner/reservation, mode and receipts are retained privately. This completes
recovery of that historical stage; current-source adversarial/reverse review
and promotion remain open.

## Finding decisions

Decisions below are local adjudication, not the still-missing Pro reverse review.
`ACCEPT` means the scoped harm is supported, not that every related boundary is
fixed. `CONDITIONAL` names a distinction/remaining condition. `DEFER` keeps the
unverified path behind the existing uninstalled/unactivated candidate gate.
No finding is rejected merely because tests already pass.

| Finding | Decision | Current evidence/action | Remaining gate |
| --- | --- | --- | --- |
| F-M04-01: stripping changes exact-NFC identity | ACCEPT | Current `classify` preserves NFC text; stripping is classification-only. Policy/retained-source tests cover distinct whitespace identities. | Current-source reverse verification and production migration. |
| F-M04-02: CRLF/LF split | CONDITIONAL | Raw exact identity and LF search projection are intentionally distinct; actual CRLF publication/retrieval regression preserves the original source hash. | Representative native text variants; do not claim projected bytes equal originals. |
| F-M04-03: policy upgrade/history/activity | ACCEPT | Staged `PolicyMigration` retains old pages, reclassifies current policy and avoids fabricated activity in bounded tests. | Production root promotion/rollback, current source visibility and broader replay. |
| F-M05-01: target-level status events withheld | DEFER | Source authority currently requires a document/version. Do not fix status visibility by blanket-authorizing `None`. | An explicit target-metadata authorization contract plus failure/pause/relation-event regression. |
| F-M05-02: first publisher's private capture metadata in search | ACCEPT | Reproduced on current 0f2e5a9 through both FTS and Korean literal search. Corrected search projection exposes only messenger content identity, preserving stored capture metadata privately. | Current-source reverse review and installed API verification; no whole-system privacy claim. |
| F-M05-03: second origin classified unchanged | CONDITIONAL | Exact shared content is still one document/version; a new occurrence is not an independent fact or author. | Verify whether origin-only changes need a scoped refresh/link receipt without fake added-content combat. |
| F-M05-04: two-database activity loss | ACCEPT | 583bee9 adds deferred stored events and durable frontier outbox; 15 commit/crash cases and 3 rejected source mutants pass. | Production backlog/liveness/source-arrival/UI/combat verification. |
| F-M05-05: retracted non-messenger relation endpoint | ACCEPT | Reproduced on 77fc305: both endpoint withdrawals leaked an edge through legacy graph, paged graph and reference counts. Relation SQL rejects retained withdrawn/unversioned owned memberships before the source guard, while preserving explicit never-owned external references. | Current-source reverse review and installed refresh; this is not a cross-transaction or semantic-truth proof. |
| F-M05-06: wrong version and unchecked origins | ACCEPT / CONDITIONAL | Current-version membership/body binding was fixed in583bee9. Origins access still needs its actual caller/scope audit. | Test every origins entry point; retain current source/account/project guards. |
| F-M07-01: Telegram anchor leaves50-row window | DEFER | Current reader correctly fails closed when the observed page cannot revalidate the anchor. That is not history liveness. | Supported exact-anchor/bounded historical lookup and actual consent-bound Telegram verification. |
| F-M07-02: numeric message-ID tie ordering | ACCEPT | Both controlled platforms reproduce wrong nearest neighbors. Connection-local total ordering compares unsigned ASCII decimal magnitude/raw ID, then opaque binary IDs, without casts, ID merging or schema changes. Both time bounds constrain the observed indexed scan. See CONTEXT_ORDER.md. | Current-source reverse review, production/native protocol evidence and large same-time groups; deterministic order is not causality. |
| F-M08-01: producer/stdio missing source authority | ACCEPT / CONDITIONAL | Producer source guards, readonly loading and cooperative cancellation cover earlier gaps. An explicit bounded stored-version lexical strategy returns one actual full-project fact/current context in1.90s on the final candidate (earlier7.47s), where complete mode reaches10s. Stale/misbound index projections are rejected. Default/coverage remain explicit; full native index is still incomplete at7,425 cached rows. See INDEXED_RETRIEVAL.md, INDEX_AUTHORITY.md and READONLY_HOST.md. | Production enrollment/authority factory, default strategy/quality decisions, broader hybrid/archived-only throughput/current visibility, Telegram build qualification/consent, current-source reverse review and installed activation. |
| L-QUERY-NUM-01: local numeric-only suffixed-token recall gap | DEFER | Controlled `100` query does not retrieve `100개` in either complete or indexed mode. Mixed Korean/unit numeric boundaries pass separately; no parser broadening disguises this gap. See INDEXED_RETRIEVAL.md. | Numeric/unit/grouping semantics and independent gold before claiming numeric-only recall; new indexed mode is explicit, bounded and unpromoted. |
| L-CANCEL-01: local long-read cancellation gap (not an original Gemini finding) | ACCEPT / CONDITIONAL | Real recursive SQL performs10,000 UDF calls after cancellation and exit-time native checks run after cancellation on the baseline. VM progress, guard checks and read/publication forwarding now interrupt/rollback; three regressions and three source mutations detect the gap. | One opaque callback cannot be preempted; native throughput/overhead, production wiring and current-source external review remain open. |
| F-M08-02: retrieval multi-transaction race/relations | ACCEPT / CONDITIONAL | Baselinef139 loses later selected entities' relations and returns a body after post-assembly permission revocation. All selected sources now share the relation budget; a fresh final collection snapshot rechecks authority, candidates, compacted proofs, source bytes and relation bindings. Real SQLite/MCP boundary cases and actual-source negative controls cover the demonstrated harm. See RETRIEVAL_BOUNDARY.md. | Current-source reverse/native integration, authority churn/retry performance and downstream use after the final read; no all-interleaving or cross-native atomicity claim. |
| F-M08-03: root mismatch/entity union | ACCEPT / CONDITIONAL | Root A authority reads a cloned root B on baseline5686877. Explicit adapter root declarations are now checked before open/create and across connections/context wiring. Current resolution members/relations check source authority, and graph post-processing compares scoped revisions. Independent copies survive. Four actual-source mutations detect each guard. See AUTHORITY_ROOT.md. | Current-source reverse/native/factory integration, migration of unbound legacy callables, historical review redaction and churn/downstream behavior. No malicious-Python sandbox or atomic cross-native claim. |

## Reproduced search correction

Two independently permitted sources can share the same exact text/version.
After Telegram is revoked, a current Kakao occurrence still legitimately
permits the body. The old `CollectionStore.search` returned immutable Telegram
`metadata.source` verbatim, including account, authorization hash and capture
dates. The existing access test even required the old platform to appear.

Search now projects known messenger source metadata to `kind`, `content_hash`
and an explicit content-identity-only scope. It does not fabricate current
source IDs or expose an old consent receipt. Current scoped source proofs remain
available through the guarded graph/retrieval paths. Other source metadata,
query/ranking/body behavior and stored versions/raw bytes are unchanged.

New regressions fail on the old implementation for both lexical branches and
before revocation. Corrected tests retain body/hash availability, original
version metadata and event counts, and deny results after every remaining
origin is unavailable. A compatibility check preserves non-messenger source
metadata. The old assertion expecting revoked metadata was replaced with the
stronger privacy/content-binding assertion, not removed.

Affected 131 checks pass. The complete consumer run passes 807 tests across 45
fresh pinned Python3.11 processes. The current publication verifier and its 3
broken variants also pass their expected outcomes. Native/private capture proof
from an older module hash is not relabeled as a new installed search test.

Full 18/ST01–16/Cursor8 remain active. Current-source external reverse/delta
review, remaining finding gates and installed/production verification must
finish before this candidate is promoted.


## F-M05-05: relation membership is source-specific

The guarded-source callback intentionally allows ordinary non-messenger
targets. Its return value cannot establish that a relation endpoint still has
a membership in that target. The old scalar subquery returned NULL after
incremental retraction, and that default callback still returned 1. If a second
permitted source kept the same document globally visible, the first source's
old edge and paged/reference edge count remained visible.

A real isolated SQLite counterexample exercised either endpoint, through
`graph`, `graph_page` and `graph_references`; all 6 assertions failed before
correction. An initial `EXISTS`-only proposal incorrectly required ownership of
every endpoint. The independent focus BFS oracle rejected 9 scope combinations
because an explicit external reference can legitimately point to a document
owned only by another source. That proposal is rejected; the independent oracle
and its expected graphs were not weakened.

At the relation SQL snapshot, the correction distinguishes two cases. If this
relation owner has a membership row, it must have a non-null current version,
be available in schema 3 and pass the existing source guard. A withdrawn or
unversioned row yields 0 and cannot fall back to another source. If no owned
row exists, the existing source guard receives NULL as before: ordinary explicit
external references remain compatible, while guarded messenger sources still
fail closed without their own version. Callers separately require both nodes
and the relation owner to be permitted. Older schema availability compatibility
is retained; no role or author identity is inferred.

Read filtering does not delete retained relations, relation versions or source
records. Re-observing the owned endpoint and its explicit relation restores
visibility. A second regression retains a different source's actual assertion
of the same pair while hiding the withdrawn owner's assertion. Shared nodes
remain available. Dedicated checks preserve a never-owned external reference
and withhold an owned endpoint whose current version is NULL. Explicitly changing
an already-withdrawn owned endpoint into a new external-reference role is not
inferred by this read rule; source-role migration needs separate authority and
re-observation evidence before promotion. The paged/reference and legacy outputs have different shapes;
a harness expectation of legacy-only `source_evidence` on paged output was
corrected to compare the actual source-bound relation ID, with its initial
error log preserved separately.

This fixes the scoped current-membership defect. It does not establish
semantic validity, independent authorship, instantaneous installed UI refresh
or multi-transaction revocation safety; those gates remain open.

The refined source passed 811 tests in 45 fresh pinned Python processes,
including the unchanged independent focus BFS oracle. No native/installed
relation refresh or current-source reverse review is implied by this pass.
