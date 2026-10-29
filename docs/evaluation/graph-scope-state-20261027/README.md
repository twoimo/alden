# Graph scope and lifecycle checks

This check targets wrong-project or obsolete asynchronous results being shown
as the current graph. It executes the real `CollectionGraphController`,
`KnowledgeRefresh`, graph parser, drilldown and DOM status writers; transport
replies and the renderer port are synthetic. It adds verification, with no
production runtime change.

Source alignment: `d89738332a9f3370d4209c2976c90fa2189dc9d8`.
See [coverage.json](coverage.json) for source hashes, writers, guards, await
boundaries and limits, and [STATECHART.md](STATECHART.md) for the review diagram.
The executable workflow is
`.agents/skills/verify-alden-graph-scope/SKILL.md`.

## Promise and observation

After a project switch or lifecycle boundary, a reply from an earlier controller
epoch must not replace the current graph or publish its result as current.
A → B → A is an epoch change even though the scope text returns to A. Successful
current replies must visibly apply, so rejecting every reply cannot pass.

The generated oracle independently records scope, lifecycle epoch and request
identity. It checks nonempty renderer writes and the visible graph, plus DOM
result/loading states after event handlers return and after drained microtasks
and timer callbacks. Each processed current reply must leave its own expected
visible node identity and ready/unavailable outcome. Aggregate replacement
counts are coverage evidence, not proof of the final visible outcome.
Renderer violations are recorded and asserted outside the
product's catch boundary. Otherwise the product could catch an assertion and
make a test appear to pass. Scope changes clear the previous graph and invalidate
the result state synchronously. A previously confirmed graph may remain visible
during a same-scope pause or revalidation; that retention is permitted.

## Execution domain

The generated test enumerates candidate input words, rather than translating the
controller into a separate model. Maximum depth is five actions over switch,
pause/resume, hide/show, dispose, successful/failed reply and timer advance.
There are two projects, at most two switches, two pause toggles, two visibility
changes, two FIFO reply completions and one 1000 ms timer advance. Initial
project-list readiness drains twelve microtasks without advancing the refresh
timer; then the DOM selects A and the actual controller must issue a read.
Sibling memory/scheduler replies are settled in this generator.

At this alignment there are 4735 candidate words. Exactly 2839 schedules complete;
1896 prefixes are pruned when a requested reply has no pending issued request.
Checks on those prefixes are still counted as observations, but the prefixes
are not advertised as full traces. One positive run records 41183 observations,
6179 issued requests, 2840 stale completions and 1047 current successful replies
applied, covering both projects. There are at most three issued requests in a
schedule; 2094 current reply outcomes are checked individually. Counts describe
this finite domain, not all possible interleavings.

The independent Astra-authored suite adds 21 fixed adversarial cases. It varies
joined memory/scheduler readiness, same-scope stop/start with five result forms,
disposed/replacement owners, late project initialization, the legacy revision
await, debounced search, entity resolution, selected temporal boundaries,
same-input affinity, detail, navigation reset and terminal history. Per case it
uses at most two controllers, two deferred slots, two nodes and no edges, and
at most 6001 ms of fake time. No private controller apply method is called.

## Atomicity and source alignment

JavaScript event-handler segments are observed after return. `read()` starts
three sibling reads, then resumes after `Promise.allSettled`; the controller's
epoch check guards the resumed result. `KnowledgeRefresh.tick()` has two
separate await boundaries: optional revision and graph read. Its own epoch check
is required after each boundary. Timer callbacks and promise continuations are
separate transitions; a source call and its awaited completion are not treated
as one atomic action. The independent cases explicitly defer siblings/revision.

The source map distinguishes executed writers from source-inspected paths.
Activity journal, quality panel and real `main.ts` startup are inspected, not
covered by these executions. Initialization suppresses disposed owners but does
not promise that every stopped owner performs zero option-list DOM mutation.
That stronger property is outside this graph-reply promise. The diagram is a
review artifact; source-to-diagram equivalence is not proved.

## Single rerun and bad variants

From the repository root:

```sh
python3 -B scripts/verify_alden_graph_scope_state.py
```

The stdlib runner uses existing Node/Vitest dependencies and new temporary
source copies. It runs 22 positive checks, four actual-source broken variants,
then the 22 checks on a fresh unchanged copy after removing the broken copies.
Its own child process groups, logs and temporary caches are bounded and owned.
The shared dependency target and main source are read-only inputs.

| Variant | Deliberately broken actual code | Required counterexample |
| --- | --- | --- |
| `scope_clear_removed` | Omit synchronous old-graph clearing in `changeScope()` | Previous-project nodes survive a later switch |
| `obsolete_epoch_reply_allowed` | Remove controller return epoch guard and refresh apply epoch guard | Earlier A reply accepted after A → B → A/lifecycle boundaries |
| `scope_result_invalidation_removed` | Omit `pendingScopeStatus()` in `changeScope()` | Prior terminal result remains after a new scope has no confirmed result |
| `current_success_erased` | Apply the nonempty reply, then clear it and publish empty | The individual successful reply no longer has its expected visible graph/result |

The epoch variant removes two independent fences. No claim is made that removing
either fence alone defeats the other. Mutation anchors must match exactly once;
source drift is inconclusive. An intentional scope-oracle failure is required;
a compilation, setup or collection failure alone earns no mutation credit.
The original broken patches, test JSON and failing action words remain in the
local receipt. The runner validates the exact 22-case denominator and no skips.

Require `status=pass_bounded_checks`, all six trial acceptances, unchanged main
input hashes/dependency link and scratch cleanup. CI runs the two positive
Vitest files with the normal desktop suite; the local single command adds the
four bad variants and restored positive replay.

Native Astra review found that the initial generated oracle checked application
counts without comparing each reply's final visible outcome. A temporary
actual-source variant confirmed that the old generated test could pass after
every successful graph was erased and marked empty. This finding concerns the
test oracle, not a reproduced defect in the current product; the companion
21-case suite was not claimed to accept that variant. The corrected generated
oracle checks exact visible request identity and result state, and the fourth
bad variant keeps that distinction executable.

## Limits and maintenance

This is bounded actual-code state exploration. It is not unbounded safety,
mathematical, refinement or liveness proof, and does not complete ST15. It does
not establish source authorization or semantic truth, GPU/rendered/native UI,
main application wiring, all temporal edge transitions, journal cursor/retry,
quality pagination, arbitrary response ordering or unbounded scheduling fairness.
The generated replies are FIFO; other selected orders exist only in the 21
fixed cases. Native app, original operational data and external audits are not
invoked. Their existing holds remain separate.

When a covered writer, guard or await changes, review its input/owner contract,
update the ledger and diagram and rerun against current bytes. Keep genuine
counterexamples. Distinguish source bugs, fixture bugs, mutation-anchor drift
and bounds changes. Record the delivered verification commit separately from
the unchanged production-source alignment above; never relabel an older built
candidate as built from a later test-only commit.
