# Graph reading levels and scaling costs

User outcome: keep the full map clear and reveal saved subjects such as
연봉·복리후생, 자소서 and 채용공고·지원시기 when zooming in. The user clarified
that the requested analysis is **asymptotic time and memory analysis**.
This is an uninstalled, reversible candidate. The full project goal remains active.

## Behavior and ownership

`hologram.ts:updateZoomLabels` owns passive caption visibility. At the fitted
overview, subjects, document titles and saved reference lists stay hidden.
Intentional hover/selection and the accessible node list remain available.
The existing relative zoom hysteresis admits detail after about1.47×; opacity
smoothly increases to full strength at1.78×. Zooming out removes passive labels.
Actual screen-space collision/overlay/clipping checks and the24-label budget
remain in place. No saved node, text, source, relationship or classification changes.

When only activity effects animate, label projection/selection now reuses its
previous result. Camera/position motion, graph candidate replacement, viewport
size, fitted distance and hover/selection invalidate it. This cache does not
authorize source reads or cache an account/permission decision.

`semantic-layout.ts:separateMultiMemberCenters` retains its exact pair/update
order. If any coordinate distance already exceeds the required separation, a
Euclidean norm calculation is unnecessary. The new axis rejection skips that
calculation. It does **not** remove the quadratic pair loop or prove optimal packing.
A subsequent [complete fresh-center plan](center-plan-20261023.md) now avoids
calling that loop when every resulting center would be overwritten. Incomplete
and incremental placements retain it; historical measurements below are unchanged.

## Cost model and limits

Let N/E be displayed input nodes/edges, K the multi-member communities, C the
eligible caption candidates and B the visible caption budget. The following
source bounds assume bounded string lengths and ordinary constant-time map/set
operations. Long-string normalization/comparison adds its own character cost.
They are component bounds, not an all-input theorem for the entire application.

| Boundary | Time | Extra retained/scratch memory | Qualification |
| --- | --- | --- | --- |
| Saved label/caption rebuild | O(N log N + E) | O(N + E) | Includes full candidate ranking and source-link witnesses; not a frame operation. |
| Moving caption projection/selection | O(C log C + CB) | O(C + B) | Sort plus greedy collision checks. B≤24; candidate lists can include up to8 overview captions beyond the2048 ranked titles. |
| Stationary caption selection | O(1) cache check | O(1) extra cache key | Entire render is still not O(1); graph buffers and visible-label placement retain their costs. |
| Caption DOM refresh | O(BN) existing lookup | O(B) labels | Each new label still finds its node in the displayed list; bounded B does not make arbitrary N free. |
| Multi-member center separation | O(16K²) when required | O(K) | Complete fresh plans now avoid this phase; incremental/incomplete plans retain it. Axis rejection reduces constants only. |
| Display correction | Bounded spatial passes | O(N) |2048 nodes,8 passes,131072 tested pairs. Budget exhaustion is reported; it is not zero-overlap certification. |

The semantic solver inspects at most8192 input nodes,32768 edges and32768
affinities, retains2048 nodes and at most4096 relation/4096 affinity pairs,
and caps community/solver passes at8/32. Those caps do not prove linear
scaling or globally optimal semantic communities. The center separation above
has a separate quadratic term. Database size, source acquisition, IPC and GPU
memory are outside these in-memory bounds.

## Matched measurements

Baseline is7d7a5149f8a05dbd913add6cf8e5cc5fa3f4adc6. Run from `desktop`:

```sh
npm run bench:labels -- 7d7a5149f8a05dbd913add6cf8e5cc5fa3f4adc6
```

The maintained runner builds the actual baseline/current label and solver
sources. Paired rounds alternate order. This measures CPU methods in Node on
the local host, excluding DOM placement, WebGL, application FPS, source arrival
latency and battery. Source/model hashes and raw results are retained under
`outputs/zoom-asymptotic-20261023/`.

| Cold chain solve | Baseline median | Candidate median | Geometry |
| --- | ---: | ---: | --- |
|128 nodes /127 edges |4.50ms |5.74ms | Same exact anchor/cluster JSON hash |
|512 nodes /511 edges |24.95ms |21.72ms | Same exact anchor/cluster JSON hash |
|2048 nodes /2047 edges |189.61ms |118.57ms | Same exact anchor/cluster JSON hash |

Nine rounds per size;2048-node p95 was242.09→149.70ms. The median improvement
for that workload is37.5%;128-node timings regressed within the observed noisy
small-workload range. This is not a universal performance claim.

For2048 label candidates,11×80-frame batches measured stationary selection
median0.627ms→below0.001ms after the first calculation. Moving selection was
0.594→0.608ms and is not claimed faster. The cache avoids repeated work rather
than accelerating every projection.

Explicit-GC retained V8 fixture measurements were approximately231KiB for512
and869KiB for2048 candidates. The128-candidate delta was negative due to GC/
allocator noise and is **inconclusive**, not negative memory use. These rough
fixture observations neither prove a whole-app memory bound nor show RAM savings.
The original zero-weight synthetic-edge fixture, which the solver correctly
ignored, is preserved separately and is not the chain benchmark above.

## Verification and remaining gates

- New behavior regressions first failed against the baseline. The final suite
  passed1105 checks in98 UI test files; TypeScript/Vite build passed. Existing
  centroid/pin/layout regression assertions remain in place.
- Four new checks cover overview/zoom/reverse zoom, intentional hover/selection,
  stationary invalidation and continuous opacity. The CPU comparison also
  rejects a changed anchor/cluster geometry hash for each tested chain size.
- Background in-app-browser inspection used an existing2048-node stored-data
  snapshot. At1×:0 passive labels. At1.7×:14 visible labels. At2.3×:21.
  Each had0 measured label/overlay overlaps and0 clipped labels, with unchanged
  source data. This verifies the compiled candidate renderer, not installation,
  fresh source collection, physical input, Retina, GPU/FPS or battery behavior.
- Current-source external adversarial/reverse verification and the installation
  gate remain open. Original reports/owned tabs and service reservations remain
  preserved. No model substitution or duplicate research was dispatched.
- Next scaling work is a deterministic spatial broad phase for K² center work,
  representative real-graph topology/resize/pan measurements and whole-app memory/
  GPU/battery evidence. Those are explicit open items, not accomplished here.
