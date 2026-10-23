# Retrieval relation coverage and response validation

F-M08-02 has locally reproduced counterexamples on baselinef139e8e. This is a
reversible, uninstalled candidate; native integration and independent current
source review remain required.

## Observable harm and correction

Relation lookup used only `chosen[0]`. Selecting two entities lost the second
entity's relation, in either selection order. Lookup now includes every selected
source, ordered by selection rank then relation ID, with one shared existing
maximum of10 relations. Both endpoint versions, relation ownership/project
permission, source authority and evidence binding remain required.

A separate real private SQLite writer revokes permission immediately after the
assembly read transaction closes. Previously the call still returned its old
body. The response now passes a fresh final read before return. Permission and
protected-source revision state are captured at request start and compared with
the final collection read. The final read also checks all returned candidate
IDs/versions, selected entity/source bytes, every compacted original proof and
the exact active relation rows and their endpoint/evidence bindings.

The guarantee concerns a fresh **collection snapshot** and the existing source
callbacks' checked interval. It is not authority that lasts forever, an atomic
transaction shared with native databases, or an assurance about changes after
the last check, MCP serialization, model use or eventual delivery. A caller must
not treat a saved result as permanent permission.

## States and actual boundaries

| State/action | Actual implementation | Guard / next outcome |
| --- | --- | --- |
| Initial scope | `_retrieval_authority` in its own read transaction | Captures requested-project target permissions and protected-source scope revisions. Denied targets do not invoke source scope reads. |
| Rank | Existing `_rows`, lexical/dense and temporal selection | These are separate reads, not one atomic operation. Existing version and source guards remain. |
| Assemble | Existing `retrieve` read transaction | Constructs facts, compacted original proofs, bounded context and selected relations. |
| Before final read | Existing cancellation check | A cancellation cannot certify a result. No cancellation callback is called after the new final read. |
| Validate final collection snapshot | `_verify_retrieval_result` | Scope differs → authorization error; candidate/version missing → version error; relation differs → relation error; source/body integrity failures retain their existing errors. |
| Return or reject | Pure response construction after final read | A failed check returns no old result. The existing read context rechecks protected callbacks on exit. |

Writers covered by injected cases are target-project permission changes,
ingest/retractions/version changes, declared relation revisions and protected
reader/grant changes. They are independent of retrieval reads. The tests inject
one change after assembly closes; they do not exhaust every concurrent writer
ordering. An ordinary permission change after the final read snapshot begins
belongs outside that snapshot's point-in-time guarantee. External native state
is not made atomic with collection SQLite by this table.

## Verification and negative controls

- The original relation regression fails in both source orders, and the late
  permission-revocation test fails because no error was raised.
- New checks cover late body revision, an unselected candidate's retraction,
  a retained-Threads compacted original's retraction, relation evidence revision,
  protected Kakao/Telegram origin revocation and actual in-process MCP response
  serialization without the private canary body.
- The first compaction fixture incorrectly used ordinary graph text, which is
  deliberately ineligible for retained-Threads compaction. Its failure is kept;
  the corrected fixture uses the existing declared author/date/original-text
  contract and first asserts that two originals really compact.
- Private copies of actual source remove final validation or limit relations to
  the first entity. The real regression methods detect7 failures and2 subtest
  failures respectively, with no test errors. Production source is never mutated.

The harness marks direct `_verified_source` calls during `retrieve`, so a body
ranking probe does not accidentally trigger the “after assembly” injection.
This boundary mapping and source hashes are retained with the receipts. These
are bounded executable counterexamples and tests, not a formal all-input or
whole-system privacy proof.

## Cost, bounds and remaining gates

Response validation adds two short scope/read boundaries, a bounded candidate
version query and repeated selected-source/blob checks. It does not load an LLM
or access native messenger bodies. The response has at most100 candidate IDs,
10 selected entities and10 relations. Initial eligible rows/text still have the
existing100000-document/32MiB text budgets; small output limits do not cap total
process memory. Authority-row results are capped by the existing100000 retrieval
budget, but SQL work before that limit is plan-dependent. Live source callbacks
can have costs outside this SQLite estimate.

`_verified_source` now uses a one-document CTE; the final candidate query uses
one CTE of ranked IDs. Both reuse `_scope_versions(..., candidates=True)`, keeping
all permission/version/source predicates inside the scoped version selection.
The purpose is to avoid repeatedly ranking every unrelated membership; no new
index, persistent table or relaxed authority decision is introduced.

The first unbounded verification candidate took65.289→86.433ms at2000 documents.
Its measurements are preserved separately. After bounded selection, paired warm
local measurements (three warmups, nine alternating-order runs) are:

| Ordinary synthetic documents | Baseline median | Current median |
| --- | ---: | ---: |
|64 |7.164ms |11.022ms |
|512 |21.128ms |24.250ms |
|2000 |61.853ms |70.560ms |

Validation has a measured cost; this is not a speedup claim. The final run keeps
an identical stable response and unchanged private store bytes. Actual executed
final SQL is captured for EXPLAIN, without asserting brittle plan wording.
These warm private fixtures do not establish native authority-churn throughput,
installed latency, RSS/GPU/battery or a worst-case SQLite complexity theorem.
Concurrent collection changes may conservatively reject a request. Usable live
retry behavior, rejection rate and latency need representative production tests.

Whole-app/stdio authority wiring (F-M08-01), root binding/entity union (F-M08-03),
native consent/source liveness and independent current-source reverse review
remain separate gates. Evidence lives under `outputs/retrieval-scope-20261023/`.
