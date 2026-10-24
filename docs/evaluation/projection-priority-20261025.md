# Preserve core separation before optional screen whitespace

Baseline:244c98ffe3333aa34480e5b670c2ae137426e19e, with the semantic-coordinate
repair already installed. Its actual1984-point compact native capture retained371
reference-core overlap pairs and exhausted131072 pair checks. Default-size capture
had0. These are anonymous projected circle cores, not source identity, glow,
font ink or semantic-quality measurements.

The display solver treated optional4.5px whitespace like an actual overlapping
core. On two explicitly separated1024-point grids (radius1.3px, spacing2.7/3px),
it created589/364 overlapping pairs. New behavioral regressions reject both.
A changed title, graph membership or embedding is unnecessary to reproduce this.

Each bounded pass now checks actual cores first with the smaller spatial grid.
Optional whitespace is considered only when that pass found no core contacts;
it is at most the existing4.5px cap and scales with10% of the visible average
cell pitch. This is a display heuristic, not a geometric packing theorem.
The final pass repairs cores. A retained nonzero correction keeps its optional
spacing choice while still allowing actual new core contacts to be repaired;
this preserves stable re-projection. No source coordinate, edge, membership,
semantic force, label policy, model, index or persistent data is changed.

The node2048/pair131072/shift10px caps stay unchanged. The pass cap is16
(previously8), allowing sparse core-only rounds to converge within the same
arithmetic pair budget. Optional whitespace uses at most the first8 rounds;
extra rounds are exclusively for actual cores, avoiding an accidental doubling
of optional whitespace work. The first actual8-pass candidate improved compact371→33
but left3 default-size contacts, so that candidate was not installed. The new core and gap
check/contact counters make admission observable. WithN admitted points andP
passes, storage remainsO(N); up to two grids are built per pass. Adversarial dense
cell traversal is stillO(P*N²), including candidates skipped before the arithmetic
pair counter. The arithmetic pair cap alone is not a full wall-time proof.
Average spatial pruning does not establish universal linear complexity. Bounded
repeated corrections do not prove all feasible packings converge or preserve
all semantic distances; the authority is still the unchanged3D source solver.

Separate8-pass prototypes compared fixed-gap core priority and density-only spacing,
then combined them. On saved *already corrected* compact screen points used as
fresh input, baseline336 residual overlaps, priority7, density0 and combined7
were observed. This is a controlled replay, not the unrecorded original input
of the installed correction and not a production speedup. Both separated grid
regressions have0 with the combined candidate (also verified in the final16-pass
full frontend run). A densely overlapping2.5px grid
remains difficult: baseline1043 versus combined1344 residual pairs for1024nodes.
That tradeoff is retained; there is no all-input non-regression or zero-overlap
claim. The displacement envelope and iterations can make dense packing infeasible.

The first full frontend run detected drift on re-projection in both motion modes.
That failure is retained and fixed by retaining settled optional spacing; the
existing frame/GPU-buffer/bridge/picking/pin checks remain the acceptance oracle.
No test tolerance was relaxed. Final checks and native/install evidence live in
the task's projection-priority-20261025 output directory, not inferred here.

The owned transparent native audit also drives distant/returned wheel input to
the actual camera maximum (rather than merely0.55 of fitted zoom), validates the
native distance and preserves the two zoom-in stages. It retains zero fitted/
zoomed-out labels, label/HUD rectangle checks, inactive/nonkey capture and final
minimized readback. It does not manipulate the user's primary window or prove
physical gestures, Retina, whole-app FPS/GPU/battery, voice or a normal release.
Current-source Pro/Gemini/reverse validation remainsNOT_RUN under the preserved
source-upload rejection. No alternate model/account/upload route is used.
