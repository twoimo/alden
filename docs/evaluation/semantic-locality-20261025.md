# Cached affinity updates preserve distant positions

Changing one admitted content-affinity pair used to reactivate residual forces
through almost every source/topic spoke in a connected graph. The existing
0.14-unit limit applied separately to each snapshot, so repeating a small change
still rearranged most of the saved map. Identical-input replays remained stable;
that check alone did not detect movement across distinct updates.

The solver now distinguishes community evaluation from position updates. With
the same admitted node set, real relation weights and pin frame, an affinity-only
delta applies position forces to its endpoints and two hops of the existing
community adjacency. The adjacency already excludes imbalanced hub spokes from
community attraction. Community membership still evaluates its original affected
component; stored nodes, labels, evidence, directed relations and affinity data
are unchanged. `affectedNodes` continues to describe that community component;
`dirtyNodes` measures actual position changes.

Cold layouts, node or structural-relation changes, explicit `changedIds`, reflow,
pin-frame changes and out-of-frame prior admission retain the existing solve.
All ordinary cached moves keep the existing 0.14-unit bound. The two-hop search
adds O(N + E) worst-case work and O(N) storage within the existing admitted
snapshot budgets. Two hops can cover an entire dense graph; this is neither a
universal locality bound nor a global packing or semantic-quality proof.

On the same saved, read-consented 1,984-node/4,096-relation career graph with
1,024 admitted affinity pairs, four scenarios alternate one omission/restoration
for 24 updates each. They use zero or three pins and either the same pair or
rotating pair indices. These are geometric perturbations of saved data, not
new ingestion or verified live source events.

| Pins | Sequence | Moved after 24 updates, before | After | Final median displacement, before | After |
| --- | --- | ---: | ---: | ---: | ---: |
| 0 | Same pair | 1,976 | 2 | 1.344601 | 0 |
| 0 | Rotating omission | 1,976 | 28 | 1.326973 | 0 |
| 3 | Same pair | 1,973 | 1 | 1.171672 | 0 |
| 3 | Rotating omission | 1,973 | 25 | 1.168869 | 0 |

Across all 96 updates the maximum per-step displacement remains at most
0.140000001. Pins, input graph and prior maps are preserved; every unchanged
replay is exact. Affected endpoint positions can still accumulate movement:
the candidate's largest final displacement is 1.586688 units. The result is
distant-position preservation, not a cumulative bound on every endpoint.
CPU/V8 durations are retained privately and establish no speedup, native pixel,
GPU/FPS, battery, physical-input or installed-app result.

Two 64-node regressions failed on the preceding source and now pass, checking
distant positions through 24 alternating updates with zero/three pins. A third
test preserves admission of a distant out-of-frame restored coordinate. Removing
that admission guard from the actual source makes this test fail; restoring
the exact bytes makes it pass. Existing focused pin, force, budget, fallback,
worker and driver tests also pass.

Six same-input comparisons against the preceding 9baa source preserve exact
cold/reflow anchors, cluster IDs and diagnostics with zero, one and three pins.

Reproduce the maintained fixture checks from `desktop/`:

```sh
npm test -- src/__tests__/semantic-layout.test.ts
```

Current local validation passed 1,228 tests across 107 files and TypeScript.
Source-replay inputs, hashes, original failures, the 96-update comparisons and
guard sensitivity are in the task's private `graph-contact-native-20261025`
output directory. They are excluded from commits and external attachments.

The existing 0.3.220/9baa binary remains an older uninstalled candidate. Its
background all-scope launch was rejected by macOS LaunchServices before an audit
process/report existed; system logs record a launchd `Operation not permitted`
spawn failure. The original app, collector, models, settings, policies, queues,
metadata and installed file manifest matched before/after. No direct-binary,
alternate-backend or permission workaround was attempted after that denial.
Installed Alden remains 0.3.219. A rebuilt source-bound candidate and actual
native/installation acceptance remain required. Current-source external
ChatGPT/Gemini/reverse review is NOT_RUN under the preserved source-upload hold;
no release, production messenger enrollment, model/account change or send follows
from these local tests.
