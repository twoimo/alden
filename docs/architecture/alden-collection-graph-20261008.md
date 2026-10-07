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
