# Store ownership and current entity-review authority

F-M08-03 has reproduced root/overlay failures on source5686877. The fixes below
are reversible candidates, not installed native delivery or independent review.

## Explicit root contract

Previously an authority created for private root A could be supplied to a cloned
root B with the same account/document/version identities. Graph, retrieval, MCP
construction and writer construction accepted the wrong binding. Identical
bytes do not transfer authority to another store. The original private fixture
reproduces all four missing rejections; it does not claim to expose a different
plaintext body or compromise a native account.

Participating Python adapters now declare `collection_source_root()`, returning
the **state root Path**, not its `knowledge/collection` subdirectory. The
canonical Frontier captures its creation root and checks its frontier/store
paths. MessengerAuthority checks its registered frontier roots against its own
root. Account/target binding remains the existing per-source responsibility;
an unavailable/changed account is still withheld rather than adopted.

CollectionStore validates declarations before creating or opening data and at
entry/exit of every managed connection. The expected root is derived from the
actual database path. Retrieval/MCP also check context-callback declarations.
Existing store objects recheck changes; a misbound writer creates no directory.
This retains the existing path policy: unmatched lexical roots are rejected;
no additional alias canonicalization is introduced.

Legacy trusted callables without this explicit declaration retain their existing
Python API behavior. They are **not certified as root-bound**. Wrappers that hide
their adapter owner also lack this declaration. Production factories must retain
or supply the contract; migration and unknown external callers remain a gate.
The declaration prevents accidental wiring errors between trusted components.
It is not a cryptographic capability or a sandbox for malicious Python code.
JSON clients cannot supply callbacks or create a declaration.

## Review/union authority

The existing resolution validity check inspected current membership/version and
project permission but omitted the native source guard. A revoked protected
member could therefore leave a decision marked current when an independent
allowed copy preserved the same displayed IDs. `_source_rows` now checks the
selected member's actual source authority before reading its source record.

Resolution relation fingerprints now use the same owner/endpoint predicate as
the graph plus permitted endpoint source witnesses. A relation owned by a revoked
source cannot keep a decision current through another source's allowed copies.
Original nodes, independent relationships, source records and stored decisions
are retained; current overlay validity changes, not historical source identity.

The resolution writer uses its own BEGIN IMMEDIATE connection. It now installs
the real shared SQL guards and checks them before commit. Adding a predicate to
read code alone was insufficient: the first candidate produced missing-function
errors on this writer, retained in the evidence. No allow-all fallback was added.

The graph read and later overlay read are separate transactions. `read_action`
now compares shared scoped permission/source-revision snapshots before reading
the graph and after projection. The existing retrieval snapshot implementation
moves into CollectionStore so the two paths use one policy. A post-graph source
revocation rejects the response; it cannot publish stale graph/union data merely
because the earlier graph transaction already completed.

This is a scoped change detector across those reads, not an atomic transaction
with native databases or perpetual authorization after return. Ordinary source
revisions/retention, historical review redaction, downstream use and factory/
stdio/native wiring still need their actual contracts and verification.
Coarse revisions may reject requests during otherwise valid collection activity;
representative churn/retry/latency and whole-app resource costs remain unmeasured.

## Real boundaries and negative controls

| Check | Observed evidence |
| --- | --- |
| Root A authority / cloned root B | Four baseline routes fail to reject; current routes, direct Frontier callbacks, long-lived owner changes, context-owner mismatch and no-directory writer checks pass. |
| Current member review after revocation | Baseline returns a current group; current code invalidates it while the independent permitted copy still displays all originals. The fixture has no active relations, preventing a relation check from masking a missing member guard. |
| Relation-owner-only revocation | Ordinary members remain available; loss of the protected relation invalidates the old review without hiding independent nodes. |
| Revocation after graph read closes | Baseline returns; current shared post-projection check rejects. |
| Actual source mutations | Removing root checks gives8 failed assertions/subtests; removing member, final graph-scope or relation-owner checks gives1 failure each, without test errors. Private module copies only. |

An early root declaration incorrectly rejected an account change as a root
error. The original account-withholding oracle was preserved and the root check
was narrowed to actual root ownership; per-source identity checks remain intact.
All830 affected checks pass in45 fresh pinned Python3.11 processes. These are
bounded code/filesystem/SQLite tests with controlled authorities, not a proof
for every input, native consent or all concurrent schedules. No source group was
filtered wholesale to obtain a pass. Evidence is private under
`outputs/authority-root-20261023/` and includes the original failures, rejected
candidate, four mutations and source-bound results.

Current-source Pro reverse verification, production authority factories,
legacy-callable migration, native integration/consent and installation remain
open. The original queued23fb94b review and earlier source packets are preserved.
