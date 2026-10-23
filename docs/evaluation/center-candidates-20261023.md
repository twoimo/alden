# Ordered center candidate masks

Base: `99b24b66e818092c2dc8a242c7195e86361c33d4`. Reversible, externally
unvalidated candidate; installed Alden remains 0.3.218. The full goal stays active.

## Problem, alternatives and resulting behavior

Incremental and incomplete fresh plans still run center separation, previously
visiting every ordered pair in up to16 passes. A prior dense synthetic component
measurement reached8,380,416 pair tests for1,024 communities. The earlier fresh
complete-plan optimization remains; this change targets the remaining callers.

| Candidate | Observed outcome | Decision |
| --- | --- | --- |
| Existing ascending all-pairs loop | Simple, useful for small/dense inputs; far pairs still cost a test. | Retain as exact fallback. |
| Moving3D cells with sorted candidate arrays | Exact outputs, but1,024 coincident communities measured99.77→426.79ms. | Reject. |
| Moving axis-bin bit masks | Avoids candidate sorting; seeks indices in the same ascending order and refreshes after moves. | Implement with bounded admission and dense fallback. |

`centerCandidates` owns a pass-local derived index; the solver still owns every
point and the unchanged push formula. The solver reports both moved endpoints.
Small sets (K<128), K>1024, unusual coordinates (initial absolute value>64 or
nonfinite), unsupported minimum distance and dense sampled neighborhoods use
the original pair enumeration. Eight deterministic rows select the dense
fallback when more than45% of their remaining indices are box candidates.
This is a performance heuristic, not a statistical/semantic density estimate.
If a reported move exceeds the guarded cell range, enumeration switches to
every remaining pair immediately, preserving the current cursor.

No node, edge, source, semantic weight, community assignment, coordinate rule,
pin, caption, rest length or activity changes. The new index changes which
already-separated pairs are skipped. Visual overlap quality is preserved here;
this change does not claim a better optimum or newly inferred relationships.

## State and source argument

Each axis maps occupied cells to member bit masks. A row intersects the union
of its three neighboring cells on each axis. Bits are visited low-to-high,
including signed bit31, and indices below the caller's cursor are excluded.
The original axis/distance predicates still decide whether an admitted pair moves.

During a row, only its point and already processed peers move. Unprocessed peers
therefore remain in their indexed cells. A row-point cell change refreshes its
neighbor masks and current word; the peer update is retained for later rows.
Newly created peer bins contain only processed indices during that row, so an
earlier empty-neighbor cache cannot omit a future peer. A retired empty mask is
reused only at a row boundary, after the previous cache has finished. This also
avoids repeatedly allocating full word arrays on every cell crossing.

| Transition | Owner action |
| --- | --- |
| Begin pass | Build guarded masks or select all-pairs; no cache crosses centroid restoration. |
| Begin row | Recycle retired zero masks; load current neighbor masks and reset word cursor. |
| Admit/push | Preserve ascending pair order/formula; update both endpoints before the next lookup. |
| Row changes cell | Refresh masks and word; earlier pair indices remain excluded. |
| Unsafe reported move | Disable pruning for all remaining rows/pairs in the pass. |
| End pass | Restore centroid exactly as before; discard this derived index. |

Cell width is minimum×(1+1e-6). Initial guarded coordinates and the dynamic
±8192-cell limit leave a conservative margin for binary64 subtraction/division
rounding, so a pair passing the unchanged axis test is included in neighboring
cells. The runtime guard precedes signed-integer coordinate storage. The index
is internal to this synchronous solver; unreported external point mutations are
outside its contract. This is a source argument with bounded implementation
tests and negative controls, not an all-input formal theorem or whole-project proof.

## Cost and memory boundary

Let K be indexed centers, W=ceil(K/32), Q admitted box pairs and P actual pushes.
Word seeking advances monotonically within each row. With ordinary constant-time
map operations, index work is O(KW+Q+P) per pass, including bounded mask recycling.
Worst-case Q and P remain O(K²): the algorithm is **not globally linear**.
Without recycling, repeated new-cell word-array allocations could add an
unwanted allocation term; the row-boundary pool keeps mask capacity tied to
live bins plus one row's retired masks, O(KW). Map/bin/coordinate objects add
their own storage. This is a source-level scratch-space bound; transient GC,
whole-process RSS, GPU memory and battery are unmeasured. No RAM saving is claimed.

## Matched CPU and compatibility evidence

Actual baseline/current sources, Node26.12.0, equal inputs, alternating order,
two warmups and nine rounds per full incremental case:

| Nodes / topology | Baseline median | Candidate median |
| --- | ---: | ---: |
|256 chain |2.222ms |2.523ms |
|256 disjoint pairs |2.070ms |2.216ms |
|512 chain |6.704ms |5.319ms |
|512 disjoint pairs |6.232ms |4.907ms |
|2048 chain |70.690ms |35.228ms |
|2048 disjoint pairs |68.954ms |34.050ms |

The2,048-node chain median decreases50.2% for this synthetic CPU workload.
Small cases regress; this is not a universal speedup. The separate component
comparison preserves exact geometry in12 cases. For1,024 coincident centers it
measures103.680→77.606ms; for512 coincident centers26.210→26.463ms, effectively
unchanged at this measurement precision. Component and complete solver numbers
are different experiments and must not be combined.

The full comparison preserves exact anchors, memberships and complete checkpoint
JSON in112 cold/pinned/reflow/incremental combinations, plus the opposing-hub
incomplete-plan case. A frozen installed-reader graph (1,984 nodes/1,212 edges)
also yields identical incremental outputs in five CPU replays. Those ordered
real-input replays include a slower candidate sample; they establish compatibility,
not representative latency distribution or installed performance improvement.

Seven new unit cases cover signed bits/word boundaries, moved row/peer state,
same-word insertion, cursor exclusion, positive/negative floating boundaries,
fallback and mask recycling. Six actual-source mutants are rejected for stale
row/peer indexes, stale word cache, revisited past bits, wrong bit order and
removed dynamic coordinate guard. Mutations use isolated Vite transforms.

UI1,114/100 files pass with four workers; TypeScript/Vite build passes with the
existing large-chunk warning. An initial default-parallel run timed out in two
settings suites' beforeAll loading waits. Both pass in isolation and the full
four-worker run passes without changing their assertions or timeouts; the
initial failure remains retained and is not called a product loading fix.

Private evidence: `outputs/center-grid-20261023/`. Re-run the source-bound
comparison there with `node compare-full.mjs`; the baseline is retained exact
99b24b6 source. Earlier failed/prototype/concurrent measurements stay separately
named. No source acquisition, native send, installed activation or foreground QA
occurs. Current-source external Pro/Gemini/reverse review, production arrival/UI,
minimized physical interaction and whole-app resource/release gates remain open.
