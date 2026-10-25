# Existing production source authority through GBrain

Ordinary native content revisions previously changed the export's authority
digest. A watcher therefore discarded its complete accepted cache even when the
enrolled account and authorization stream remained unchanged. CLI collection
openers also omitted the optional production stream callback, while the explicit
`kakaotalk` option referred only to an existing evaluation root.

Exports now separate the declared authorization stream from the complete content
checkpoint. A stable authorized publication can use the existing source archive,
graph index and engine delta paths. Account, enrollment, project permission and
authorization changes continue to invalidate incompatible data. Full native
content/relation scopes, per-record access, source comparisons and closing
snapshot checks remain required. Unavailable current content fails the complete
export and refresh removes accepted generations; it never becomes a partial
successful graph. A changed digest policy safely requires one new full baseline.

`KnowledgeServer.collection_store()` retains an optional stream callback only
from the same authority instance as the other bound callbacks. The four-key
search/retrieval guard API remains unchanged. Default production autoguards and
the explicit factory both use the existing exact root/project binding.

| CLI option | Source contract |
| --- | --- |
| `--messenger-source enrolled` | Validate an existing active production enrollment; lazily open only requested enrolled targets |
| `--messenger-source kakaotalk` | Existing private evaluation-root factory, unchanged |
| No option | Existing ordinary collection guards, including the existing production auto-binding when present |

The new option is exposed by collection export, full import, bounded cache,
watcher and knowledge MCP entrypoints. It never creates enrollment, discovers or
changes an account, grants a project, starts a service or registers an MCP.
Withdrawal remains confined to the owned cache and requires no working provider.
Cached production sources/memberships recheck the expected account. Revision
reads check identity around authorization/content batches and after relation
scopes, before caching content. These fences do not claim a cross-database
atomic account lock.

## Verification

The pinned Python 3.11 suite has 20 new regressions for lazy production binding,
missing/prepared/cancelled enrollment, graph-only scope, bound stream ownership,
default/explicit parity, stable-content versus changed-policy epochs, withheld
content and account changes before/during cached content/relation reuse. Seven
isolated mutants are detected by assertion failures; the actual source checkout
is never mutated by those controls. Related source/graph tests and fresh-process
messenger/relation suites are recorded in the adjacent JSON and task receipts.
CI runs the new tests in their own process.

The executable harness uses a private production root with real enrollment,
Frontier collection/publication, current-source guards, the reviewed actual
GBrain binary, engine/proof readback, watcher CLI and independent stdio MCP.
Only the canonical native reader is replaced by an isolated file fixture.
There is no evaluation marker. A separate writer commits the controlled reader
and Frontier publication before each watcher observation; this is a controlled
snapshot experiment, not a native live-service concurrency measurement.

Two published trials use `engine_delta` and `journal_delta`: replacing a message
content identity puts one page, purges the old page, adds one membership link
and preserves three unaffected opaque revisions; adding a message puts one page
and adds one link. The index updates/deletions and full source comparison agree.
Ten MCP calls exit cleanly: six current authorized paths are accepted and four
stale/withheld/wrong-account/policy paths are rejected. Content withholding,
account switch and policy controls leave zero accepted generations after
refresh, and each controlled restoration rebuilds current data. Final explicit
withdrawal leaves no active pointer or generation. The packaged scripts repeat
the same harness separately; see the package receipt for that result.

## Limits and original state

Metadata-only inspection of the original five project scopes found 43 declared
targets, no production messenger enrollment and no evaluation marker. This is a
declaration count, not a count of currently authorized sources. No original
body/SDK read, enrollment, DB write, source/service restart or account change
was performed. A real native account/device run, service installation, installed
GUI acceptance and ordinary release remain open. Alden stays off at the user's
request; protected collector/model processes and previous app candidates remain
preservation targets.

Only logical row mutations are selective: full authorization/source comparisons,
index regeneration, temporary seen-key state, DB copy/hash and complete engine
readback still cover the store. No end-to-end complexity, latency, memory, token
or quota saving is claimed. The reviewed GBrain and open-ontologies pins/design
references remain those in `gbrain-scoped-paths-20261026.md`; no OWL, SHACL,
conservativity theorem or certificate engine executes here. ChatGPT/Gemini/reverse
review remains NOT_RUN under the inherited coordinator/source-upload holds.
The required independent web-Astra/max worker is unavailable and was not
substituted. This is an externally unvalidated candidate; all original
18/follow-up/ST01–16/Cursor gates remain active.

Task evidence: `outputs/gbrain-native-authority-20261025T232701Z/` contains the
source/package harness, source hashes, public failure controls, process receipts,
test logs, protected-state comparison and delivery readback. Adjacent JSON binds
the evidence hashes without including source message bodies or credentials.
