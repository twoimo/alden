# Explicit bounded stored-version retrieval candidate

Based on `408fd9cc685b452570580d6cd0a4252750c11e9a`. Reversible and externally
unvalidated; installed Alden remains 0.3.218. This is a new explicit search
strategy, not promotion of a different default/model or completion of the full
native/dense index, production enrollment or all 18 requirements.

## Result, choice and contract

The previous actual full-project search timed out before returning a fact.
Inspection disproved one initial hypothesis: completed text-cache entries already
avoid rewriting their extracted text. The main retrieval cost was checking the
entire eligible source corpus before lexical candidate selection.

| Candidate | Benefit | Cost / coverage |
| --- | --- | --- |
| Existing `complete` strategy | Preserves current hybrid, retained-text and corpus-wide readiness behavior. | Native source checks scale with the eligible corpus; the observed full-project lookup exceeds its 10s limit. |
| Explicit `indexed-lexical` strategy | Ranks stored-version index slots, then admits bounded current source candidates and reuses the existing fact/proof/context assembler. | Stored-version lexical coverage, bounded admission and no dense search; not exhaustive or archived-only-text search. |

The second option is implemented as an opt-in candidate. `retrieve` and
KnowledgeServer still default to `complete`. The MCP CLI accepts
`--search-strategy indexed-lexical`; a trusted programmatic source host can set
the same startup option. Request JSON cannot switch strategy or provide authority.
The search tool description and results declare the narrower **search coverage**;
the source corpus/project/room scope is not narrowed. Provider/model routing is
unchanged and this strategy performs no embedding-profile/model calls.

New result fields are `retrieval_scope=bounded_stored_version_index`,
`eligible_documents_scope=admitted_candidates` and an explicit coverage statement.
The existing `eligible_documents` value counts admitted candidates in this mode;
it must not be displayed as the corpus size. Empty bounded results do not prove
that no relevant source exists. Existing complete-mode results keep their shape.

## Source and version boundaries

Raw index selection returns only IDs, versions and scores. Index document ID,
label and body must match the bound current stored version; stale or misbound
FTS slots cannot supply relevance for another projection. It applies existing
current-membership/project-permission/time predicates; it does not confer native
authority or expose bodies, raw metadata, rejected IDs or rejected counts in a
response. This follows the existing search-index-before-source-filter pattern.
The derived index can contain a source whose current authority is withheld.

Admission then runs the ordinary `_scope_versions` guard in small candidate-key
queries. The returned authorized current version must equal the version that
actually matched the index. A withheld matching version cannot supply the score
for a different permitted nonmatching version. Rejected top candidates cause
bounded refill, rather than an immediate empty response. Selected/pinned IDs are
read again, and the existing raw-source/projection, current membership, relation,
native context and final fresh response checks remain in force. Authority/root
binding is checked before the index is opened and across its existing connections.

These are separate source/read intervals, not cross-native atomicity, a malicious
Python sandbox, perpetual permission or proof of all-source visibility. A source
change during the request can cause rejection/omission; the caller may need to retry.

## Budget and asymptotic limits

For requested candidate count C (1..100), the raw token/literal slot budget is
`min(1000, max(100, 10*C))`. Native admission uses batches of at most 20 document
IDs. The final bounded row read allows C plus at most 40 explicitly pinned IDs
(maximum 140), with the existing text/context/source limits unchanged. Refill is
bounded even if high-ranked slots are withheld or outdated.

Source-authorization work is tied to these candidate/membership batches and the
selected proofs instead of a preceding full `_rows` read. This is not constant
total SQL work: FTS ranking still depends on matching postings/versions, membership
fanout and sorting; literal fallback can inspect stored version text across the
corpus. Opaque native callback cost and final context acquisition remain. No total
RSS/GPU/battery reduction or all-size latency bound is claimed.

## Actual full-project stdio comparison

Two sequential fresh pinned Python processes used the same existing Kakao
evaluation root, query `회의`, result limit 1, 10s tool budget, canonical native
reader/root authority and current-source context callback. No document or room
allowlist narrowed the 18,661-document retained corpus. The text cache remained
at 7,425 rows and neither run collected new source data.

| Strategy | Observed request time | Outcome | Source checks | Canonical body calls |
| --- | ---: | --- | ---: | ---: |
| `complete` | 10.010321s | Deadline; no fact | 2,058 before stopping | 0 |
| `indexed-lexical` | 1.897487s | 1 fact and 1 current native context window | 322 | 2 |

Both rejected an unauthorized project and request-level strategy injection,
answered a subsequent ping and exited 0 after EOF. Network/embedding calls were
zero. Canonical body calls reported readonly/schema validation and zero source
writes/sends/settings/server calls. Both derived source DB logical hashes and
cache row counts remained unchanged.

This is the final-code ordered workload pair, not a distribution, independent semantic gold
or an exact speedup ratio: the complete result is censored at its deadline and
the strategies have different declared search coverage. It establishes one real
full-project lookup that now finishes within the existing budget. It does not
prove all queries, complete hybrid/archived-only parity or installed delivery.
An earlier candidate pair, before the FTS projection checks, observed10.007949s
deadline versus7.470742s success. It is retained separately. The selected source
IDs/versions are equal across both candidate runs, and the actual native store
has zero mismatched FTS slots. Timing variance is not attributed to the integrity
checks; no average/p95 or stable speedup is inferred from these two pairs.

## Regression and negative controls

Seven new tests cover a 602-document protected corpus without a full native scan,
fail-closed unguarded use, refill past withheld top slots, matching-version
binding, Korean literal/mixed numeric-unit boundaries, post-assembly permission
withdrawal, MCP startup wiring, explicit coverage and stale/misbound FTS slots.
The last regression first reproduced a valid but irrelevant source fact from a
forged/stale index slot; current document/label/body bindings reject it. Existing
default complete tests remain. All 853 affected checks pass in 45 fresh pinned processes.

Eight isolated actual-source variants are detected: removing the final candidate
row bound, refill, matching-version binding or final response proof gives one
assertion failure each. Each of the three FTS document/label/body guard removals
also gives an assertion failure. Removing MCP strategy forwarding gives a missing coverage
key error. That error is retained as a detected API mismatch, not a clean assertion
pass. Checkout, installed and native source code were never mutation targets.

The initial refill test used the nonexistent `body` fact type instead of the
established `entity` type; that oracle error/log is retained. A later numeric
fixture queried bare `100` against `100개`. A dedicated controlled reproduction
shows the same recall gap in both existing complete and new indexed strategies:
the Korean literal plan does not support a numeric-only substring query. The
mixed unit boundary (`10만원` versus `110만원`) is separately tested and passes.
The parser was not weakened to make the fixture green. Numeric-only/unit/grouping
semantics remain a named open condition, not a completed numeric-search claim.
An early consumer sweep was interrupted after that fixture failed; its logs
remain separate from the final 852-check receipt.

## Remaining gates and evidence

Independent human relevance/semantic/temporal gold, numeric-only semantics,
large denied-hit/fanout workloads, representative latency/resources, default
strategy decisions, production source enrollment/wiring, full text/dense index
completion, Telegram decoder/consent, external reverse/delta and installation
remain incomplete. No new graph nodes, ingestion events or decorative combat
were produced by these reads.

Private evidence: `outputs/indexed-retrieval-20261023/` contains source hashes,
default/focused/final tests, initial failures, five source variants, exact private
pipe responses/canonical receipts, native preservation and the numeric gap.
Scoped public source packets exclude that corpus/identity/session data. Original
23fb reverse request and prior packets remain immutable; this new strategy is
prepared for consequential delta review, not independently validated.
