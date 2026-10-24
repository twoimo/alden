# Alden 0.3.29 collection graph

The settings graph now reads the actual versioned collection store. It offers project, source, target, type, relationship and collection-date filters, text search, bounded pages and a visible list with the same node selection. Existing OSK/Kakao memory remains available through the project selector.

Each overview admits at most 120 nodes and 512 stored edges. A remote neighbourhood admits at most 24 nodes and 144 edges, up to three hops; the existing renderer further limits each node's expansion to 10 neighbours. Both the displayed counts and full permitted filter counts are visible. No source relationship is inferred from spatial proximity. Node size uses unique permitted neighbours, `clamp(.018 + .007 * log1p(degree), .018, .055)`, with the degree scope disclosed. Decorative backdrop stars are no longer instantiated in the graph scene.

Source selection, filtering and hiding fence late reads. Navigation retains bounded history with query offsets, selected IDs, hop depth and camera targets. Ordinary refresh preserves the camera; source changes and explicit navigation use the requested view. The graph supports rotation, pan and zoom, a separate camera reset and reduced motion. Gesture testing in the primary process remains a separate verification step.

Selected records use an exact permitted membership/version lookup. The application verifies the immutable record blob hash and fences a changed content version. It shows collected text or the retained record's JSON when the text field is empty, with a12,000-character reading budget. Evidence contains the record hash, collection time and a verified source-capture manifest pointer when available. The manifest receipt is distinguished from rechecking the whole archived file. Shape/hash verification does not establish semantic truth. Spark's protected original bodies remain outside the metadata projection.

The native read-only candidate loaded all four sources: 26,622 records and 48,355 relationships. Its all-project overview displayed 120 real nodes and 25 relationships at 1200×728 and 640×648. The owned WKWebView exercised page advance, node reading, a one-hop neighbourhood with 11 nodes/19 edges, back navigation, compact layout and resume. Fourteen workspace captures completed without overflow; memory readback was settled with no visible error. Both graph sizes were visually inspected. Two process-local visibility notifications produced zero further renders over 350 ms; observed post-to-stopped upper bounds were 12.94 ms and 14.29 ms. These are synthetic notification observations, not physical OS-lock or Retina tests. First-frame samples were 5 ms and 3 ms (n=2), not a whole-app p95.

Verification: 23 Python collection contracts, 374 UI tests and 8 focused Rust audit tests passed. The normal local build produced an ad-hoc signed 0.3.29 bundle; all 47 bundle files were hashed and its two collection modules match the source. The initial full native attempt timed out after publishing the workspace images. The later candidate passed the complete native audit under the unchanged 96-second deadline, with bounded notification diagnostics and durable workspace readback. The initial failed run remains in the private task evidence.

The final SQL comparison used five interleaved repetitions per variant against the unchanged actual store and committed `081e0c3` baseline:

|Scope|Before median|After median|Before displayed edges|After displayed edges|
|---|---:|---:|---:|---:|
|All projects|1411.47ms|1559.38ms|0|25|
|Career|778.95ms|812.65ms|0|512|
|Jangsin|158.46ms|59.27ms|108|115|

Selection policy, edge budget and metadata payload changed. The all-project and career query medians increased 10.48% and 4.33%; the Jangsin median decreased 62.59% in this small sample. These observations do not establish general latency, rendering, quality or quota improvements. Power and ambient load were not controlled, and no p95 or confidence interval is claimed. Both endpoints of the run had the same event sequence 133115. Private timing samples and source hashes are retained in the task outputs.

[Aggregate native evidence](alden-collection-graph-readback-20261008.json) contains counts and artifact identity, without raw records or screenshots. Screenshots stay in the private task output directory. At this candidate checkpoint the primary installed app remains 0.3.28; normal PR/CI delivery and installation of 0.3.29 are the next gates.

Remaining full-goal work includes durable event-driven activity/reconnect recovery, stable layout and gesture measurements at representative sizes, source-order/deletion/conflict reconciliation, scheduler execution, cross-host skill/MCP flows, Dense/RRF quality, physical voice/shortcut/lock/Retina checks, independent evaluation/DPO, full CLI contract remediation and signed release/production verification. This slice does not complete the full Alden objective.

## Installation and focus-query follow-up

Commit `6e56330ecc0c03302094fe0ad68b2753fe8df70f` reached PR27 and all four CI jobs passed in run37650572045. The normal installer applied0.3.29 to `/Applications/Alden.app`; all47 files match the candidate. Primary PID83255 and the existing model/embedding PIDs6464/11016 are alive. The three actual configuration files under `bujamentor` match the previous0.3.28 preflight byte backups. The immediate0.3.29 preflight had mistakenly selected the parent directory and found no configuration files; its empty check is not preservation evidence.

Primary CUA observation and app inventory subsequently timed out; no process was restarted for that observation failure. A separate read-only audit from the installed executable captured the default graph and first note, then failed its five-second settling check. An actual installed-module probe reproduced all-project neighbourhood reads at5512.60/5161.95/4825.45ms for1/2/3 hops. This contradicts a claim that installed navigation is fully verified.

The0.3.30 source now reads the permitted relationships once within the consistent transaction, builds deterministic bounded BFS, then fetches the selected records. It does not copy protected original bodies or change persisted graph/index rows. The tradeoff is a temporary in-process adjacency map of the permitted edge scope; memory and larger-scale behaviour still need measurement. Cycles, parallel edges, self edges and exact hop boundaries have a regression contract.24 collection tests and8 focused audit tests passed.

The actual-store equivalence run compared all response fields with the committed0.3.29 implementation:1hop6713.92→1605.06ms,2hop5354.88→2192.84ms,3hop5572.34→1441.00ms. Each pair returned the same24 nodes/53 edges and content versions. These are three different cases with one pair each, not a p95 or a general speed claim. The0.3.30 bundle/build/install/readback gates remain pending at this checkpoint.

## Installed 0.3.30 readback

The normal installer applied0.3.30 after all four CI jobs passed at `baa0b1047079efee19e8e5650f6c39b6d4f539b1` (run37655820941). All47 installed files match the candidate, strict/deep signing verification passed, and the three actual `bujamentor` configuration files match the immediate preflight backup. Model and embedding services retain their existing PIDs. The installer stopped and replaced only its managed Alden service, after verifying the old process and preserving a full bundle/configuration backup.

The earlier installed-module probe had generated one `__pycache__` file in the0.3.29 bundle. No original bundle file changed. That generated cache was moved to private recovery evidence; the original47-file manifest and signature were verified again before update. Subsequent probes disable bytecode generation with `-B`.

The installed executable's owned native audit completed successfully. It exercised14 actual workspace views at default/compact sizes, the all-project graph, selected source evidence, bounded expansion and back navigation. The installed default graph and note screenshots were visually inspected. Two synthetic visibility observations found zero further renders over350ms; observed stopping upper bounds were13.20ms and13.78ms. [Installed aggregate evidence](alden-installed-graph-readback-20261008.json) records the scope without private pixels or source bodies.

This proves the installed bundle's read-only data/renderer path. CUA observation of the primary process previously timed out, so direct gestures and physical voice/shortcut/OS-lock/Retina remain unverified. Signed production, real activity/reconnect recovery, quality evaluation and the other full-goal requirements remain open.
