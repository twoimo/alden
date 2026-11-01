# Bounded semantic-affinity allocation

The installed 0.3.116 runtime remains `45d4d6c7a2c29d80b0b5bc116a4ad960f444e550`. Version 0.3.117 is a separate candidate. Source, offline geometry, candidate/native inspection, installation and independent semantic quality are separate gates. Task evidence is in `outputs/affinity-allocation-20261015/`.

One permitted read-only product page admitted 1,228 current E5 vectors from 1,984 requested nodes. The private 1,886,208-byte vector snapshot retains version/profile/proof hashes without copying source bodies. All offline work reuses that snapshot. Replaying the original source reconstructed the actual product's entire 1,024-candidate output exactly, including cosine values and ordering. The original 65,536 comparisons and node-local top-three union of 2,959 candidates were preserved.

Global score truncation left many admitted nodes without returned affinity. The candidate allocates from that same validated union in three score/ID-ordered passes: two previously unrepresented endpoints, at least one unrepresented endpoint, then remaining capacity. Every selected endpoint has at most four affinity candidates, matching `SEMANTIC_LAYOUT_LIMITS.affinityDegree`. The returned subset retains the original score/ID order. A capacity-constrained star can return fewer edges than the requested cap; truncation is always union size minus actual returned size. Policy and endpoint/node counts are reported in coverage metadata.

| Same-snapshot measurement | Original | Joint incidence/degree candidate |
| --- | ---: | ---: |
| Returned affinity candidates | 1,024 | 1,024 |
| Backend incident eligible nodes | 531 | 1,227 |
| Backend maximum endpoint degree | 20 | 4 |
| Effective engine affinity edges | 611 | 1,024 |
| Effective engine incident nodes | 491 | 1,227 |
| Mean raw cosine | 0.949075 | 0.931299 |
| Minimum raw cosine | 0.932891 | 0.764680 |

Incidence refers to returned/effective semantic-layout edges. Existing factual relationships, rendered-node visibility and persistent label selection have their own contracts. The candidate uses only previously admitted pairs, preserving permissions, source/version/text/vector bindings, threshold 0.65, sample/product limits and cancellation. It does not establish semantic correctness, identity or an optimal coverage solution.

The initial incidence-only experiment still lost 160 edges and 43 incident nodes at the consumer's degree-four pass. Applying the same endpoint bound upstream removed that second-stage loss in the measured snapshot. The initial experiment and its lower-score tradeoff remain retained.

The actual semantic-layout engine was also exercised with the installed bounded 1,984-node page and 3,789 raw R records; 3,761 R pairs survived its existing relation checks. The admitted versions matched the vector snapshot. All tested anchors were finite/bounded, input order was independent, inputs were unchanged and repeating an identical snapshot preserved anchors exactly.

Geometry results are mixed. Cold-start world-distance pair counts below0.04 changed18→12 and below0.16 changed3,308→3,253. Starting from identical R-only anchors, asynchronous S arrival changed counts below0.04 from11→22 and below0.16 from3,500→3,668; median movement changed0.0837→0.1215 world units. A transition from the old S selection to the candidate also changed many anchors. These are bounded engine/world-coordinate observations, not screen overlap, installed frame times, physical input or a claim of uniformly better layout. Native/render and incremental-stability verification remain required.

Two requested `chatgpt-web/gpt-6-astra`/max agents reviewed supplied code/observations in parallel. They did not inspect files. The reviews identified the two-stage degree loss, star/small-cap limits, score tradeoffs and a four-node path counterexample to optimal coverage. The joint-bound follow-up confirmed the unique/non-self-pair precondition and called out return-construction guard checks. Return construction now checks every64 candidates and after completion. The local fixtures cover ties/input permutations, scope/threshold exclusion, ordering, small caps, degree saturation, cancellation during selection/return, four linear guard scans and the actual cross-language endpoint-limit contract. The final focused affinity Python suite passed86 tests; UI885, TypeScript, serial Rust145 and strict Clippy also passed.

The installed CLI's compact graph response declares `record-keys-v1`. An initial inspection assumed object rows and failed with an AttributeError. The already captured response was decoded using its declared key table with unique-index/type checks; no endpoint retry or source-code inspection was used. That error and both raw/decoded artifacts are retained.

Independent human/source-based semantic evaluation, primary physical UI/voice, representative resources, the held selected-node label read, and signed production release remain unfinished. The complete 18-item goal stays active. Source-only candidate implementation is not installed delivery.
