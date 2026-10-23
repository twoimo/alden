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
| F-M05-05: retracted non-messenger relation endpoint | DEFER | Existing protected-source tests are insufficient to prove all unguarded-source membership cases. | Reproduce independent-source endpoint retraction and enforce current endpoint membership without borrowing another target. |
| F-M05-06: wrong version and unchecked origins | ACCEPT / CONDITIONAL | Current-version membership/body binding was fixed in583bee9. Origins access still needs its actual caller/scope audit. | Test every origins entry point; retain current source/account/project guards. |
| F-M07-01: Telegram anchor leaves50-row window | DEFER | Current reader correctly fails closed when the observed page cannot revalidate the anchor. That is not history liveness. | Supported exact-anchor/bounded historical lookup and actual consent-bound Telegram verification. |
| F-M07-02: numeric message-ID tie ordering | DEFER | Equal-time numeric IDs require a protocol-aware order; lexicographic TEXT is insufficient as a general numeric claim. | Reproduce with equal timestamps; avoid fixed-width integer overflow or reordering opaque IDs. |
| F-M08-01: producer/stdio missing source authority | DEFER | Fail-closed standalone behavior is not complete dense/MCP integration. Explicit guarded consumers exist separately. | Production authority factory/producer/index/MCP wiring and unavailable-source behavior. |
| F-M08-02: retrieval multi-transaction race/relations | DEFER | Per-read guards and existing tests do not prove all cross-transaction revocation paths or relations for every selected entity. | Actual boundary injection/recheck and multi-entity relation coverage. |
| F-M08-03: root mismatch/entity union | DEFER | Callback-to-store root binding and entity post-processing need complete caller evidence. | A second private-root counterexample and revoked alias/union regression before promotion. |

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
