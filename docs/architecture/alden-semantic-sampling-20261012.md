# Bounded semantic comparison participation

The previous evaluator spent a partial comparison budget on the first ID rows.
This gave early records many more opportunities to enter layout candidates.
The evaluator now uses tournament matchings within each admitted project,
rotates project priority between rounds, and deduplicates shared-project pairs
with a bounded triangular bitmap. At 2,048 nodes the bitmap is at most 262,016
bytes. Project admission, source/version/profile checks, threshold, per-node
top-k, candidate cap and CPU/wall/RSS/product budgets are preserved.

The dot product uses `sum(map(operator.mul, x, y))` in the pinned Python runtime.
Candidate scores and the 1,024 returned records match the preceding round-robin
implementation exactly on the frozen actual inputs. The traversal changes
which partial pairs are examined; it does not change or regenerate vectors.

Wire diagnostics separately report the sampling method, eligible/compared
vector nodes, min/max per-node comparisons and node/pair coverage. Historical
missing metadata remains null/unreported. The TypeScript adapter rejects
contradictory counts or ratios. Full node participation remains
`bounded_partial` when pair work or returned candidates are truncated.
These statistics are available through internal render diagnostics without
new product controls. Layout candidates never become fact edges or merges.

The first native compact observation retained an earlier controller result
while its actual graph had no enrichment. Fresh, revalidated results are now
compared with the graph actually shown, rather than only with the controller's
cache. Missing enrichment reports pending work without retaining the earlier
completion diagnostic. A regression fails against the prior source when an
identical valid reply must re-enrich a restored graph. Existing scope/epoch
fences and version/permission validation remain in force.

## Actual fixed-input evidence

An installed read admitted 1,483 existing E5 vectors, dimension 384 and
2,277,888 vector bytes. The same read verified retained canonical records and
their text/vector/version/permission bindings. It did not open the original
full files or generate embeddings. A singleton has no permitted peer; 501
other requested records were excluded from content evidence.

For the same 65,536 comparisons and 25,165,824 dimension products:

| Observation | ID-row baseline | Project matchings |
|---|---:|---:|
| Eligible vector nodes compared | 1,482 | 1,482 |
| Min/max comparisons per eligible node | 3 / 525 | 23 / 90 |
| Nodes among returned candidate endpoints | 447 | 619 |
| Eligible pairs searched | 65,536 / 358,522 | 65,536 / 358,522 |
| Pair coverage | 18.28% | 18.28% |

Five alternating isolated processes used identical captured proofs/vector
bytes and budgets. Median pair-evaluator time was 1.1233→0.6547 seconds;
median process peak RSS was 47,333,376→47,366,144 bytes. These exclude source
admission, installed UI, GPU/battery and human semantic quality. Intermediate
slower candidates and measurements concurrent with other probes are retained;
the final measurement is separate from those observations.

One live candidate request reached the existing eight-second CPU limit while
other probes ran. A subsequent independent request completed under the same
budgets and retained the exact graph/input/provenance binding and admitted
proof records. No limit was increased. Production/default installed checks
are a separate delivery gate; an isolated benchmark is not that gate.

## Verification boundaries

Independent tests cover full pair uniqueness, overlapping/disjoint projects,
odd/even groups, deterministic ordering, exact complete cosine results,
product/pair ceilings, cancellation and partial-state reporting. Adapter tests
cover new metadata, explicit old unreported metadata and contradictory tuples.
The pinned CPython 3.11.9 / SQLite 3.46.0 suites pass. Two existing WAL-sidecar
tests failed in a separate system CPython 3.9.6 / SQLite 3.51.0 execution;
that observation is retained without changing the read-only policy or pin.

The result improves partial candidate discovery participation. It does not
prove the remaining nine duplicate candidates, entity identity, original
file equality, outlier causes, whole-graph semantic gold, central screen-space
overlap reduction, or representative whole-app latency/resource gains.
Existing R/S holds, source data, vectors, queues and model settings remain.
Private evidence is under `outputs/semantic-sampling-20261012/` in the task root.
