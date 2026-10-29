---
name: verify-alden-graph-scope
description: Check bounded actual-code graph scope and lifecycle schedules, with isolated broken variants, after changes to Alden graph refresh ownership or result-state invalidation.
---

# Verify Alden graph scope

Read `docs/evaluation/graph-scope-state-20261027/README.md`, its statechart and
coverage ledger from the repository root. The safety promise is that an earlier
asynchronous graph reply cannot replace the current project's graph or present
its result as current after a scope or lifecycle epoch boundary. A → B → A still
rejects the first A reply. Current successful replies must also visibly apply.

Production source inspected and aligned: `d89738332a9f3370d4209c2976c90fa2189dc9d8`.
The ledger records exact source hashes and await boundaries. Runtime files were
not changed for this verification addition. Every run records the actual HEAD,
source bytes, dependency versions and bounds; a recorded baseline is not a pin
for silently accepting changed code.

Run this single command from the repository root:

```sh
python3 -B scripts/verify_alden_graph_scope_state.py
```

Use the existing menu Python 3.11 runtime and `/opt/homebrew/bin/node` for local
delivery verification. `--node` selects an existing runtime; `--output` selects a
new evidence directory. The default is an ignored, timestamped `runs/` directory
beside this skill. Nothing is installed. An existing output is never overwritten.

The command executes both real-controller suites in isolated source copies:
one generated-schedule test and 21 independent adversarial cases. The generated
domain is depth ≤5, two projects, ≤2 project switches, ≤2 pause toggles, ≤2
visibility changes, ≤2 FIFO replies and ≤1 timer advance of 1000 ms. It includes
4735 candidate words; only 2839 finish as executable schedules. The other 1896
prefixes request a completion before any pending read exists and are counted
separately. The independent cases cover sibling awaits, revision probes, search,
detail, affinity, navigation, terminal history and selected time boundaries.

Require current code to pass all 22 checks, four deliberately broken variants
to fail with the intended scope-oracle message, and a fresh positive replay
after broken-copy removal. The epoch variant removes both independent reply
fences. The fourth variant erases a current successful graph after applying it;
the oracle must check each reply's final visible identity and result state,
not merely count replacement calls. Compile/setup errors alone do not count as
a rejected bad variant.
Require unchanged main source bytes and dependency symlink and removed scratch
copies in `verification.json`. Inspect the JSON fields and failure traces, not
only the process exit code.

For a genuine current-code failure, preserve the counterexample and distinguish
a product bug, fixture error and changed input contract. For an obsolete mutation
anchor, review the source transition and update the discriminating variant;
never weaken the promise just to pass. When covered source or writers change,
review all await splits, update the source map/statechart/bounds and rerun.

These are bounded generated-input checks against actual code, not a theorem,
refinement proof, liveness proof or complete ST15 certification. They do not
execute native startup, GPU layout, source authorization, source truth, original
databases, installed UI, activity/quality workflows or external release review.
Preserve all independent native, source, audit and release holds.
