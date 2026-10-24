# Additive digit-lexeme retrieval

Based on `ff09246a0c92ebacca246d74738906ee7c5bc8f4`. Reversible source
candidate; installed Alden remains 0.3.218 and external reverse/delta review
and promotion gates remain open. This partially addresses L-QUERY-NUM-01.

## Reproduced behavior and contract

The retained baseline fails the new API regression in both complete and
indexed-lexical strategies: query `100` misses the stored text `100개`.
The supplement now admits an exactly matching digit lexeme from stored label
or body, within the existing candidate budget. Existing token/dense candidates
and their precedence remain. Both strategies reuse current source authority,
version binding, source/projection validation, context and final proof checks.
Neither the Korean query parser nor direct CollectionStore/graph search changes.

Recognition requires a query of 2..64 ASCII digits, after whitespace stripping.
No numeric conversion occurs; `00100` remains distinct from `100`. A body digit
run and any following comma/dot-separated digit runs form one opaque lexeme.
For query `100`, the supplement admits `100개`, but not `1000개`, `100.5개`,
`100.0개`, `100,000개`, `1,100개` or mixed/fullwidth digit runs. A later valid
occurrence still matches after an earlier invalid occurrence. Signs are not
interpreted and units are not compared or converted. This is recall support,
not exact numeric-value filtering: existing FTS token hits for `100,000` and
`100.5` remain returned and marked `bm25_token` as before.

Supplemented facts carry `lexical_match=integer_digit_lexeme`. A recognized
query adds `integer_query_policy` explaining the additive, non-value-converting
policy. Existing protected-source rejection and saturated token candidate pools
remain intact. The default strategy and all provider/model settings are unchanged.

## Cost boundary

Let D be scoped stored versions, T their total label/body characters, C the
requested candidate budget and M the memberships inspected during admission.
The numeric fallback uses the existing bounded slot/refill mechanism, but its
predicate can scan stored version text. Its text-matching work is O(T), in
addition to query planning, joins, ordering and current membership/source work;
bounded output does not imply bounded SQL work or O(C) total runtime. The regex
matcher streams matches without building a corpus-sized list. The existing
complete strategy can still materialize scoped rows, and neither whole-process
RSS nor all-size latency is established here. No numeric side index/schema
migration is introduced without evidence on workload and maintenance cost.

## Verification and actual native boundary

The affected suite passes 856 checks in 45 fresh pinned Python processes. Three
new regressions cover API recall/legacy token compatibility, protected-source
rejection, candidate precedence and the explicit query/body lexeme table.
Four isolated source mutations are detected by assertions: disabled recognition,
substring matching, first-match-only handling and missing indexed forwarding.
The checkout and production data are never mutated by those negative controls.

Two fresh stdio processes use the same full retained 18,661-document Kakao
evaluation project, query, result limit 1 and 10s tool budget. The controlled
query was selected from a currently permitted native source: six ASCII digits
followed by text, with zero pre-existing token-index slots. Neither document nor
room is passed as a narrowing filter. Query, source IDs and body remain private.
The baseline loads exact ff09246 retrieval bytes with unchanged dependencies.

| Retrieval source | Observed request | Facts / current native context | Source checks / canonical body calls |
| --- | ---: | ---: | ---: |
| Baseline ff09246 | 0.033469s | 0 / 0 | 0 / 0 |
| Candidate | 1.690816s | 1 / 1 | 8 / 3 |

Both processes answer a later ping, exit 0 and make zero network/embedding calls.
Canonical reads verify readonly/schema boundaries and zero source writes,
sends, server calls or app-setting writes. Both staged source DB logical hashes
remain unchanged; derived text-cache row count remains 7,425. This is not a
before/after cache-data hash. Installed 78-file SHA/modes, strict/deep fixed
signature and all 43 target policies/settings remain unchanged.

This deliberately qualified single-query pair demonstrates added recall, not
a speedup, relevance distribution, independent human gold, new acquisition,
production enrollment or installed delivery. Value/sign/unit/grouping semantics,
other numeric interfaces, representative resource measurements and current-source
external reverse verification remain open. Private receipts, baseline failure,
source variants and native responses are under `outputs/integer-query-20261023/`.
