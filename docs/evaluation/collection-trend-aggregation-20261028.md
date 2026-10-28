# Collection trend publication aggregation

Deferred stored receipts previously ran a correlated publication lookup for each
receipt. A completed target run can contain many receipts, so those lookups
revisited the same journal repeatedly. The candidate materializes scoped stored
receipts and publication proofs, then aggregates semantic receipt identities.
The time window still applies after canonicalization.

The response schema, checkpoint bounds, source authority, retained-only coverage,
unknown source/backfill coverage and unclassified counts remain the contract.
The query retains the target membership predicate in its publication scan: its
indexed plan matters even though the predicate is logically redundant with the
scope join.

Behavioral regressions found during development were corrected:

- Publication validity follows the SQLite extractor's JSON5 capability. Libraries
  before 3.42 use strict JSON validation without a new function requirement.
- Window inclusion is computed using SQLite comparisons, including persisted
  nonnumeric TEXT/BLOB timestamps.
- Malformed needed publication evidence raises, including for a canonical group
  outside the window. A matching proof encountered first short-circuits later
  malformed rows. The diagnostic reverse-scan setting preserves its ordering.

Final local acceptance uses the pinned Python 3.12.13 / SQLite 3.53.1 runtime.
One relevant combined run passed 336 tests, including 16 existing trend methods,
21 receipt/oracle methods and 8 portable regression methods. The new two modules
are included explicitly in the focused CI command.

Independent isolated comparisons matched the baseline in 52/52 proof cases and
67/67 date/window cases. These overlap with regressions and are not an exhaustive
equivalence proof. An earlier monolithic local test run had the same 238 failure
identities as its baseline (26 failures, 212 errors); that is not a green full
suite or evidence about the final candidate's CI.

The current original-data old/new experiment did not enter SQL: existing WAL/SHM
sidecars were absent throughout a bounded 45-second admission window. There were
0 observations, 0 accepted pairs and 0 SQL opens. Its process was reaped without
signals; app-off, observed service presence/absence and settings were preserved.
No live speedup, memory reduction or whole-app latency claim is supported.

Original-data result equivalence and matched cost measurement remain required.
Resume after the existing collector naturally opens the store; preserve the
failed attempt and current read-only/query-only, authority and cancellation
guards. Do not create original sidecars, restart services or weaken the admission
policy to obtain a measurement.

The independent formal external audit, installed/native verification, signing
and release remain held. Alden stays off; the installed application is unchanged.
The full 18 requirements, follow-ups, ST01–16 and Cursor residual gates stay active.
