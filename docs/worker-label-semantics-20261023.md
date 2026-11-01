# Preserve saved label semantics across the layout worker

The layout driver replaced every saved label with its node ID before posting a
snapshot. The existing engine uses labels to keep generic routing buckets from
becoming overview stars and to place duplicate display titles. Direct engine
tests and the production worker input therefore described different layouts.
The driver's cache key also omitted labels, so changing a title without changing
ID/version could reuse an obsolete result.

The driver now retains the saved label in its local worker payload and includes
it in the snapshot key. This preserves existing engine rules rather than adding
a new clustering algorithm or changing identity/relationship data. Descriptions,
facts, edge context and evidence-message text remain excluded from the worker
payload. The worker stays local; no model, external upload or provider call is
introduced.

Five behavioral regressions fail before the correction: three saved-label
fixtures with duplicate titles, completed cache invalidation and a late reply
after a title-only change. They exercise the actual worker entry and retained
engine over a structured-clone port fixture. Direct and worker layouts now agree,
same-title nodes with different source targets retain all identities, successful
cache reuse remains, and superseded replies are rejected. This is a synthetic
transport test, not native Worker scheduling, semantic truth or identity merging.

The original Pro thesis raised F-ST05 on source `60003be`. A separate local
characterization on source `2110c17` reproduced 31 differing anchors for a 256-node,
30-positive-edge fixture when labels were replaced with IDs. Early 120-node and
zero-weight fixture mistakes are retained as harness errors, not product tests.
The production correction addresses this locally reproduced input-fidelity
defect; the external Gemini/reverse stages and wider algorithm decision remain
incomplete.

A read-only snapshot from installed 0.3.216 contains 1,984 collection nodes and 1,212
stored edges. Replaying that frozen bounded snapshot through the actual driver,
worker entry and engine with a simulated port produces the same anchors as
direct calculation, without changing IDs/labels/versions/targets or input bytes.
It excludes separate affinity enrichment and the unified conversation-memory
union; it is not whole-database collection, live source acquisition or semantic
gold. The initial 2,048 request was rejected by the existing collection 1,984 cap;
the cap was preserved.

For this snapshot, JSON wire size is 2,152,304 bytes versus 2,119,337 bytes for the
counterfactual prior ID-label rewrite: 32,967 additional bytes. This characterizes
UTF-8 JSON size only, not structured-clone allocation, timing, RSS or app speed.
No performance benefit is claimed.

The 0.3.217 signed build, installed file/config readback, native rendering and
exact-head CI have separate receipts under private
`outputs/worker-labels217-20261023/`. Original source records, targets, permissions,
intervals, pins and recovery artifacts must remain intact. Full graph semantics,
physical interaction, matched resource measurements, messenger lifecycle/live
integration and normal production release remain separate goal gates.
