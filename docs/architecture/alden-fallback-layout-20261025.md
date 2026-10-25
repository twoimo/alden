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
The complete UI suite passed 1,199 tests in 105 files, and TypeScript passed.
These are finite checks, not an all-input nonoverlap proof.

Source, logs, private input/position snapshots, initial harness failures and the
comparison alternatives remain in the task's
`outputs/layout-career-hubs-20261025/`. The initial 120-node harness output is
partial and is not the full-page evidence; use `fixed-scope/comparison.json`.

Native rendering, installed delivery, physical input, GPU/FPS, whole-app resource
and semantic quality are separate acceptance stages. No performance gain is
inferred from one engine timing. The current-source external Pro/Gemini/reverse
audit remains incomplete; original source-upload restrictions remain in force.
