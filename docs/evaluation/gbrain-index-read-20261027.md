# GBrain expected-index connection reuse

Full native import verification now owns one readonly expected-index connection
for its outgoing-set checks. It reuses the connection only inside one `_verify`
call. Public `verify_outgoing` and `verify_outgoing_provenance` retain their
one-shot signatures and connection ownership. This changes no cache format,
source identity, engine RPC, currentness rule or publication gate.

## Ownership and rejection boundaries

The private `_verify_outgoing_from` helper receives only the connection opened
internally for the exact projection path and the original invocation's file
token. It compares the pathname to that same token before and after every set,
including empty sets. It never repins an open file descriptor. The final verifier
token check remains, including the zero-page and long-context paths. The token
checks device/inode/size/mode/mtime/ctime; these sampled checks are not an atomic
proof against arbitrary same-user filesystem races or a new WAL-writer contract.

SQL count and point-lookup cursors close before lazy provider callbacks. There
is no explicit read transaction across native calls. The connection closes on
normal, iterator, cancellation, SQLite and validation exits. No connection is
stored on the projection/cache or shared across generations or threads.

All page enumeration, revision/source binding, outgoing shape/source/origin,
context, duplicate and complete-count checks remain. Bounded long-context
provenance still requires the separate full graph traversal/bind. Original-source
fresh sessions, visibility/currentness, cache leases and child-reaping gates are
unchanged. Import page iteration still closes batch SELECTs before revision
acknowledgments commit.

## Matched component observation

Baseline production source: `bbfc957cebb3d5fd8a98e995a2a68659a050827d`.
Candidate importer source SHA-256:
`39476afd00b43d5ffb3cd513e3835f2c71e29033405e4eb72120b55ed3639ce2`.
Fixed derived-index SHA-256:
`9256effb9b8a46505c33fe070c8dab8388783748f7d5c8722c21918a756d6edf`.
Runtime: Python 3.11.9, SQLite 3.46.0, arm64 macOS.

Both modes use the same frozen 26,694-page / 75,006-link index, input rows, runtime,
checks and per-page outcomes. Four observations per mode alternate in ABBA/BAAB
order. The lightweight connection counter performs no extra SQL query or retained
connection allocation inside the timed section. Earlier prototype and heavily
instrumented measurements remain separate task evidence.

| Outgoing comparison component | Median seconds | Observed range | Connections in this component |
| --- | ---: | ---: | ---: |
| Baseline one-shot lookups | 5.49257 | 5.13296–5.64951 | 26,694 |
| Invocation-owned shared lookup | 2.34479 | 2.17146–2.52536 | 1 |

The median reduction is 57.3% **for this local component**.
Every per-page result vector and total link count matches; all copied input
hashes remain unchanged. The row fixture is index-derived, not an independent
native or semantic oracle. Native RPC, complete refresh, current-source
publication, app/GPU/FPS/battery and token/cost gains are not established by this
measurement. Four samples per mode are observations, not a broad guarantee.

For P pages and L received links, the same P outgoing count queries and L
binding lookups remain. This removes P connection openings from the full
verifier's outgoing path; it does not reduce all validation to constant work.
Other counts/pages/bind connections retain their existing lifetimes. A fresh
seen set remains per outgoing set; no global accepted-row cache is introduced.

## Verification and rerun

```sh
PYTHONPATH=scripts python3 -B -m unittest tests.test_alden_gbrain_index_read -v
```

Seven bounded synthetic cases exercise the real refresh/verifier with four
pages/five links: connection and cursor lifetime, a competing exclusive writer,
byte-identical path replacement before and during sets, empty-set mutation,
cancellation/provider failure and both public wrappers. Existing paged-link and
import suites retain malformed/missing/extra/duplicate/provenance, long-context,
large-origin, delta/stage and source-withdrawal coverage. The focused CI suite
includes the new module.

The parent additionally ran 810 related checks across 28 modules with zero
failures/errors/skips. An isolated in-memory mutation of the actual helper
removed both per-set path guards; the three intended replacement/empty-set tests
failed, without setup errors or a production-source edit. This is sensitivity
evidence, not a theorem or full ST15 proof.

Task-local private receipts include `actual-component-final-result.json`,
`related-test-result.json`, `guard-sensitivity-result.json` and the exact rerun
harness/input hashes. The native Astra review is a bounded conditional design
review; its separate test worker wrote the seven cases. Neither replaces held
web-Astra/Gemini/formal/release review. Native fixed-copy readback and exact-head
CI are delivery gates recorded separately; installed/native GUI acceptance and
latest-source activation remain outside this component claim.
