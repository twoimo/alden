# Avoid overwritten center-separation work

This reversible candidate removes a demonstrated redundant quadratic phase in
fresh overview placement. It is not installed, and current-source external
reverse verification and normal promotion remain open.

Follow-up: [ordered candidate masks](center-candidates-20261023.md) implements
the previously deferred incremental broad phase after comparing and rejecting
sorted moving-cell arrays. The original source/measurement receipts remain intact.

## Source-derived decision

At baseline207e518 the engine separates multi-member centers, then overwrites
them with fresh energy-ray placement. Timer-only instrumentation on copied
source measured about62.08ms in that first phase within a133.14ms synthetic
2048-node cold chain solve (1022 multi-member centers). This is an instrumented
CPU profile, distinct from the paired uninstrumented measurements below.

The energy planner only writes centers; it never reads the separated positions.
It now returns a map of planned positions. The caller skips separation only if
that map covers all current centers, then applies the same positions in the
same order. Every plan key belongs to the current membership partition: ring
and dust labels come from membersByLabel, and spoke labels come from the same
clusterIds used to construct membersByLabel/centers. Thus the plan is a subset
of current labels; equal cardinality establishes complete coverage here.

There is a necessary exception. A spoke can have opposing hub pulls whose sum
has length≤1e-8. The old planner leaves that center untouched, and its final
dust pass also skips it because the pull map contains its label. An incomplete
plan therefore still runs the **entire original sequential separation** before
applying its covered positions. Skipping the whole phase unconditionally is
incorrect. Partial separation of only unplanned labels would also change their
interactions with subsequently overwritten centers.

Nonfresh, single-focus, small and incremental paths retain their old separation
behavior. No graph node, edge, source, semantic weight, rest length, pin, stored
coordinate or caption policy is changed. This is elimination of unused work,
not a new force law or globally optimal packing claim.

## Costs and measurements

For K communities and E admitted pairs, fresh planning retains its existing
O(K log K + E) work and an additional O(K) map of references/positions. With
complete coverage, the prior O(16K²) separation is absent. Incremental and
incomplete-plan cases still contain that quadratic phase. The entire solver,
community detection, label work and GPU path have separate costs; this is not
a whole-app linearity or RAM-saving claim.

Paired uninstrumented runs use actual baseline/current modules, three warmups,
nine alternating-order measurements per size, short synthetic chain scenes and
the same host Node26.12.0 runtime:

| Nodes | Baseline median | Candidate median |
| --- | ---: | ---: |
|128 |5.160ms |4.533ms |
|512 |22.917ms |17.894ms |
|2048 |166.009ms |80.832ms |

Small-scene differences are noisy;128 nodes do not enter the optimized branch.
The2048-node paired median decreased51.3% for this fixture. This is not measured
installed UI latency, acquisition-to-graph latency, WebGL FPS, RSS or battery.

## Verification

The source-bound comparison covers112 combinations:1/64/128/255/256/512/2048
nodes, chain/pairs/stars/random topologies and cold/pinned/reflow/incremental
journeys. Exact serialized anchors, cluster memberships and complete checkpoints
match baseline207e518. This finite coverage is evidence, not an all-input proof.
A pre-existing installed-reader snapshot (1984 nodes/1212 edges) also produces
identical complete outputs in five background CPU replays without changing its
input bytes. Those sequential runs establish compatibility on that frozen real
input, not representative real-world speedup or new native acquisition.

A separate264-node scene has eight30-node clique hubs and one24-node spoke
connected equally to opposite ring hubs. Its nine communities and full snapshot
match the baseline. A real source mutant removing the incomplete-plan guard
changes that spoke; the regression checks its independently captured baseline
coordinates. The cold512-node snapshot regression also preserves the baseline
complete output. Unit/golden compatibility and performance receipts prove
different things; the mutation specifically checks the conditional fallback.

All1107 UI tests across99 files, TypeScript and Vite build pass. The build's
existing large-chunk warning is retained. Native minimized interaction, actual
production graph arrival, representative whole-app resources and independent
current-source review remain open. Private evidence and original errors are
under `outputs/center-broadphase-20261023/`; no native chat bodies were acquired.

The spatial-grid alternative was considered but not implemented: preserving
sequential in-place pair order requires updating neighbor coverage after moves.
Eliminating a fully overwritten phase resolves the observed fresh-path cost
without introducing that new mutable spatial index. A future spatial broad
phase for the remaining quadratic paths still requires its own evidence.
