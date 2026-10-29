# Equal-time context ordering and bounded time scans

F-M07-02 is locally reproduced and corrected in the isolated messenger candidate.
It is not installed or activated. The current-source Pro reverse review and
production delivery remain open; the queued 23fb94b packet keeps its own source.

## Observable correction

Previously TEXT comparison placed ID `10` before `2`. With same-time messages
`2, 3, 10, 20`, the actual SQLite context query returned `10, 2` around anchor
`10` with one preceding/following slot. Both controlled KakaoTalk and Telegram
readers reproduce this failure. Telegram fixtures supply an explicit controlled
grant; they do not establish native Telegram consent.

The query now orders by timestamp, then one total ordering of raw IDs:

1. Nonempty unsigned ASCII decimal IDs, by magnitude and then binary raw ID.
2. All other IDs, in binary order. Signed, Unicode-digit and namespaced IDs are
   opaque; no unsupported protocol interpretation is inferred.

Remove leading zeroes only in the temporary comparison key. Compare canonical
digit count, then digits, then the original ID. For equal digit count, the first
differing digit determines magnitude: its positional contribution exceeds the
largest possible opposing suffix. Different digit counts determine magnitude
directly. Raw-ID ordering breaks equal-magnitude ties without merging `01` and
`1`. All key components have total orders, so their lexicographic composition is
transitive, including mixed numeric/opaque IDs. “Numeric if both, otherwise
lexical” would fail this property (`2`, `10`, `1a`). No integer conversion,
fixed-width overflow or Python digit-conversion limit is involved.

Both selection predicates and both ORDER BY clauses use the same connection-local
collation. Every Frontier connection registers it. Schema/index/storage identity,
source bodies and timestamps remain unchanged; an ordinary SQLite connection
can still read and integrity-check the file without registering a collation.
Equal-time ordering is deterministic presentation, **not conversational causality**.

## Time bounds and asymptotic limits

The older predicate already implies `timestamp <= anchor`; the newer implies
`timestamp >= anchor`. Making those redundant bounds explicit lets the observed
SQLite 3.46.0 plan constrain both ends of `(room, sent_at, message)`. Previously
each query constrained only one end and could scan unrelated earlier/later rows.
Result equivalence follows from those implications, including a zero-second gap.

Private real SQLite query-pair measurements use short synthetic IDs, null bodies,
one warmup and seven repetitions. They exercise the actual SQL extracted from
Frontier, not source acquisition, full context decoding or the app renderer.
The measured bounded statements exactly match the final source.

| Rows in room | Spread times, prior one-sided bound median | Spread times, both bounds median | All rows equal-time, both bounds median |
| --- | ---: | ---: | ---: |
| 1,000 | 0.151 ms | 0.078 ms | 1.832 ms |
| 10,000 | 1.309 ms | 0.158 ms | 16.614 ms |
| 50,000 | 6.989 ms | 0.091 ms | 84.102 ms |

Spread-time queries use a 30-second gap and return the same six neighbors. The
plan uses both time bounds, but still reports a temporary sort for the ID term.
These warm-cache samples are not a speed guarantee or an FPS/RAM/battery claim.

Let R be room rows, H the rows in the indexed time interval, L the largest ID
length, and B the returned context items (at most 11 including the anchor).
Under the observed indexed plan and bounded row/key widths, the expected cost
model is an index seek plus interval scanning/sorting, rather than a half-room
scan. A conservative comparison-sort estimate is O(log R + H log H * L), not
O(B); SQLite's actual LIMIT strategy may perform less work. A large same-time
group can still have H=R. No persistent numeric index or all-input SQLite
complexity theorem is claimed.

Comparison keys use O(L) temporary Python space and no retained key cache.
SQLite sort space depends on H and row width and may spill to disk. Context
decoding additionally retains up to B distinct source pages, each constrained
by the existing 4 MiB serialized-page input cap, plus Python/JSON overhead.
The final 64 KiB item cap does **not** cap peak parsing memory. Native RSS and
whole-app resource measurements remain open.

## Verification and remaining gates

The focused suite checks nearest neighbors, numeric/opaque mixtures, zero
padding, IDs beyond signed 64-bit and 5,000 digits, reopen, time precedence,
cross-page source revalidation, source edits, permission changes, body budgets
and legacy timestamp migration. Context reads leave the frontier bytes and
event journal unchanged in the ordering fixtures. Existing source/account/grant
checks still apply; an ID order never authorizes a body or creates a graph fact.
All 815 affected consumer checks pass across 45 fresh pinned Python 3.11
processes on the final source, including the 21 focused context checks.

Evidence is private under `outputs/message-order-20261023/`: initial test-fixture
authorization error, corrected two-platform failure baseline, focused results,
consumer results, exact source/query binding and all measured samples/plans.
The initial measurement harness selected the authority wrapper instead of
Frontier; its failure is retained and the corrected harness names Frontier.

Production Telegram consent, protocol-specific causal/reply ordering, large tie
group optimization, native UI/ingestion latency and independent current-source
reverse review remain separate gates. This local fix does not resolve all
messenger authority, retrieval TOCTOU, alias or semantic-quality findings.
