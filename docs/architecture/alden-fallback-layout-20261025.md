# Fallback layout before collision solving

The dense career overview exposed a stage-order defect: saved-topic stars and
equal-label fallback positions were written after the bounded collision solve.
That could pack nearby nodes together again. Fallback geometry now supplies
initial positions to the same solve, rather than replacing its output.

Only nonsemantic saved-topic hub centers become fixed geometric region anchors.
They are counted separately as `fallbackAnchorNodes`; they are neither user pins
nor evidence of a semantic relation. Leaves remain movable. Equal-label fallback
positions are soft targets. Admitted semantic endpoints, user pins, inactive
components and completed positions retain their authority. Explicit reflow can
reseed ordinary completed positions, while preserving semantic endpoints and
user pins. The solver budgets, node membership and source relations do not change.

The label rule is unchanged: passive topic and saved-title captions are hidden
at the fitted overview and maximum zoom-out. They fade in after approximately
1.47 times the fitted magnification and reach full opacity at 1.78 times.
Selected/hovered nodes remain readable through their separate active-label path.

## Finite comparison and regression

The actual permitted career page contained 1,984 nodes and 4,096 source-topic
connections. It contained no duplicate relationship groups. Cached E5 comparison
admitted 1,024 pairs after 65,536 of 1,967,136 possible comparisons (3.33%); this
is explicitly partial semantic coverage, not a semantic gold evaluation.

The production parser and `KnowledgeDrilldown.current()` supplied every one of
the 1,984 nodes to the baseline and corrected engine. For the admitted-affinity
condition, whole-snapshot pairs closer than .07 world units fell from 213 to 10;
closer than .14 fell from 1,870 to 919. Under the fitted 1,200 by 680 front camera,
pairs closer than four pixels changed from 316 to 285, and the largest 24-pixel
cell from 14 nodes to 11. With the same camera, four-pixel pairs changed from
316 to 267. The central 100-pixel population increased from 30 to 69: the result
reduces peak overlap, but does not establish reduced total central mass.

With relationships alone, .07-unit pairs changed from 10,366 to 60 and fitted
four-pixel pairs from 2,233 to 335. That separate condition must not be substituted
for the cached-affinity comparison. Unchanged repeat drift was zero in both.

Four behavioral regressions cover dense fallback placement, explicit reflow,
semantic-hub authority and exact user pins/repeat state. An isolated actual-source
check passed these four plus 67 existing layout cases; restoring the late
overwrite failed two cases and removing the semantic guard failed its case.
The complete UI suite passed 1,201 tests in 105 files after the transport
correction below, and TypeScript passed.
These are finite checks, not an all-input nonoverlap proof.

Source, logs, private input/position snapshots, initial harness failures and the
comparison alternatives remain in the task's
`outputs/layout-career-hubs-20261025/`. The initial 120-node harness output is
partial and is not the full-page evidence; use `fixed-scope/comparison.json`.

## Native transport regression found during acceptance

The first signed candidate timed out before any scoped layout could run. The
installed 0.3.218 control completed the same two-size diagnostic, with zero
passive labels in overview, maximum zoom-out and after returning from zoom.
The candidate's exact career packet exceeded the unchanged 4,194,304-byte Rust
boundary: the existing lossless `record-keys-v1` transport needed 4,821,445 bytes
after the new relation evidence fields were included.

Large packets can now use `record-keys-v2`. It shares nested evidence/metadata
field names and uses a tagged dense row only when every declared key is present.
Sparse rows retain their explicit indices, so absent and null remain distinct.
Unencodable nested values retain the previous representation. The consumer
accepts both versions, validates field names, indices, dense lengths and allowed
nested tables, and builds fresh objects without inheriting prototype-named keys.
No source fields, claims, identifiers or revisions are omitted.

The exact retained packet is 4,116,581 bytes with v2. Its production TypeScript
consumer restores all 1,984 nodes and 4,096 edges field-for-field and does not
mutate the wire input. Python and UI cases cover sparse/dense rows, null/missing,
future fields, prototype-named fields and malformed nested metadata. The old
source is retained as the failing over-budget control. This qualifies this
specific packet; it does not guarantee that arbitrary future pages fit the
native limit. Native acceptance is rerun on the newly signed artifact and kept
separate from this source/consumer check.

Native rendering, installed delivery, physical input, GPU/FPS, whole-app resource
and semantic quality are separate acceptance stages. No performance gain is
inferred from one engine timing. The current-source external Pro/Gemini/reverse
audit remains incomplete; original source-upload restrictions remain in force.

## Small positive weights: arithmetic state and regression

`1 - 1 / (1 + w)` rounds sufficiently small positive weights to zero. The ensuing
zero degree ratio produces NaN, and the actual worker can return
`semantic_layout_failed` rather than a completed layout. Use the equivalent
`w / (1 + w)` to retain representable positive inputs. Normalization can still
underflow a contribution beside a stronger edge, so an exactly zero total
stiffness is counted as `numericallyInactivePairs` and excluded from the solver.
Its source relation remains in the input and rendered graph.

| Arithmetic state | Solver transition | Source/evidence meaning |
| --- | --- | --- |
| Invalid or nonpositive input | Existing validation rejection | No new authority |
| Positive bounded/normalized stiffness | Mix rest lengths using stiffness ratios first | Existing relation/affinity retained |
| Exactly zero normalized stiffness | Count numerical inactivity; omit that force | Source relation is retained; no outlier deletion |

For admitted finite positive relation weights, the bounded value is positive
and at most one; degree sums are bounded by the admitted pair count. Admitted
semantic contributions are finite and nonnegative. A retained pair's positive
total permits finite ratios in [0,1]. Compute `r/total * rRest + s/total * sRest`
instead of multiplying tiny stiffness by a rest length before division. This
avoids losing the subnormal product and retains a finite positive rest length.
The real and semantic rest ranges are [.3,.75] and [.27,.62], up to floating
rounding. These are local arithmetic assumptions and bounds, not a whole-app,
concurrency, semantic-quality or universal layout proof.

Six actual-engine regressions failed before the correction. They cover four
isolated positive scales, underflow beside a stronger relation, and a semantic
contribution below representable scale. The actual retained worker entry plus
structured-clone driver also returns finite completed coordinates. Source
objects are unchanged. The full UI suite passed 1,208 tests in 106 files and
TypeScript passed. An isolated source check passed 77 layout cases and detected
all three broken variants: subtractive transform, zero-force division and an
underflowing rest product. These suites overlap and must not be summed.

On the same full 1,984-node page, the maximum coordinate difference was
4.28e-15 with relationships alone and 8.67e-14 with admitted cached affinities;
the measured world/pixel close-pair counts were unchanged. This demonstrates
the finite ordinary-input comparison, not bitwise equality for all inputs.

## Central-mass alternatives remain experimental

Six source variants compared weaker hub-spoke attraction, soft leaf region
targets, collision pressure and local sampling rotation. Each used the same
retained full page with and without admitted cached affinities. None satisfied
all stated central-quarter, .07-unit, four-pixel, repeat-state and admitted-pair
neighbor-rank criteria. No force/rotation alternative was applied to the
product. Emptying the center while concentrating points elsewhere is not an
accepted result. Neighbor ranks describe only admitted-pair geometry and do
not constitute independent semantic gold. Task evidence and failed alternatives
remain in `outputs/central-affinity-balance-20261025/`.
