# Collection SQLite connection ownership

Alden0.3.213 closes collection policy, scheduler and realtime SQLite
connections deterministically, including failures before a context yields.
Previously, acquisition-policy reads used SQLite's transaction context manager
without closing the connection. Scheduler initialization and realtime schema/
read-only setup also occurred before their closing `finally` block. Legacy
scheduler recovery connections had the same transaction-versus-close gap.

`contextlib.closing` now owns each connection immediately after creation. The
existing commit/rollback rules, actual backup operation and integrity checks
remain. Policy reads retain `mode=ro`, `query_only` and their150ms timeout;
realtime reads retain their200ms timeout, and writes retain their5s timeout.
No schema, policy values, schedules, permissions or source data change.

The18 real-SQLite ownership regressions cover valid/default/invalid policy
returns, query/schema failures, both actual legacy recovery files, backup/
integrity-query failures, successful commits, interruption rollback and
read-only write rejection. A tracking connection retains references only to
observe whether the function itself closes; the fixture then releases every
owned connection even on a failing baseline assertion.

Installed0.3.212 fails12 of those18 checks with no test errors. Corrected
source passes104 ownership/acquisition/receiver/scheduler/realtime/discovery
checks on pinned CPython3.11. Exact signed staged/installed resource probes,
artifact hashes/signature, static-state preservation and existing-service
adoption are separate delivery gates recorded with the PR receipts.

This proves deterministic ownership, not production RAM growth, GC lifetime,
a cause of graph stutter, resource savings or a DB-engine selection result.
The uninstalled channel-discovery candidate, external research, broader DB
lifecycle/retention and the full project objective retain their existing gates.
