# Alden multi-target collection — current execution boundary

The collection journal retains independent source identity, immutable original snapshots, version history, target/project membership, per-target locking, transactional checkpoints and ordered persisted stage events. Failed batches roll back documents, indexing and checkpoints; failure history remains. Exact source replay does not create another version. Semantic similarity does not merge authors or entities.

Actual configured metadata sources were read: employment public graph, Spark privacy-filtered derived index, 장사의신 and YouTube 쯔양. 쯔양 is a distinct project from Tzudong. Their existing stores remain canonical; this is a separate logical derived projection.

[Full configured-scope readback](alden-collection-baseline-20261007.json) reports26,622 nodes,48,355 explicit source relations and zero in-scope orphans. One run per source imported407/1118/12561/12536 records and402/1116/22388/24449 relationships in.315/.965/22.285/65.263 seconds respectively. Source extraction/indexing only; these timings are not independent semantic review or a before/after speed claim. Stored stages confirm FTS indexing only; Dense and application visualization need separate receipts.

The common skill has one canonical user source with two discovery links. Its current adapter contracts consume declared Threads snapshots, existing YouTube/source graph JSON and the derived Spark SQLite index. SQLite Online Backup handles the live derived WAL index without changing its journal or forcing a checkpoint. Original protected files, credentials and message targets are unchanged.

Initial collection unit contracts:8 passed. Actual bounded replay of5 source records reports0 added,0 revised,5 unchanged; version count26622 remains26622. This is a bounded replay check, not a complete incremental-refresh benchmark.

An actual OSK receipt node was created and re-read in the verified Spark scope:261007-1dyd-bpdjy6m2. It preserves a derived-from link to the existing file graph source. This one receipt does not mean all26,622 records were transferred into OSK or the final logical graph contract is finished.

## Schema 2 and application connection

The read-only application boundary now offers project-scoped graph pages (up to120 nodes), focus neighborhoods (up to24 nodes and144 edges), and ordered history pages (up to200 stages). Readers never create an empty store. Target, source, project, stage and time filters apply before results reach the UI.

Each target membership retains its own stored version. A different target's revision cannot replace the content visible in another project, including when both targets reference the same original ID. Search uses the permitted membership's version. Schema1 upgrades retain a private SQLite Online Backup, then migrate lineage, version search and the schema marker in one transaction. Interrupted migration rollback and retry are covered.

The actual task-private store was upgraded with26,622 documents/versions/memberships,48,355 relations and133,135 events preserved. Digests of all original table fields match the recovery snapshot, and every referenced immutable source file matches its recorded SHA256. Integrity and foreign-key checks passed; unresolved membership versions:0. This preserves the existing derived store; the independent source stores remain authoritative.

The memory-history controller has a single visible-page timer, virtualized rows, source links and stage details. It distinguishes shape validation from semantic truth, unchanged content from additions, and FTS indexing from Dense indexing. Forward keyset reads recover more than one page after hiding; stale filter/hide responses are discarded. Older browsing stays stable while new stage counts are announced. Open evidence details survive unchanged polling and insertion.

Current local verification:15 Python collection contracts and365 UI tests passed, including8 collection-history tests. The two collection modules are in the bundle resource allowlist. Collection CLI runs observe the existing global abort epoch; cancelled batches roll back their documents/index/checkpoint and record a distinct paused stage.

The actual application data directory now contains the four declared sources:26,622 documents,48,355 explicit relations,133,110 stage events and four complete target checkpoints, with integrity passed and zero orphans. A candidate0.3.28 ad-hoc bundle loaded those records through its frozen read-only bridge. The owned native WKWebView showed10 virtualized history rows at1200×728 and8 at640×648; both were ready with no visible read error. Both private screenshots were inspected for clipping and readability. Navigation first-frame samples were13ms and17ms (n=2, not a p95 or a whole-app latency claim).

[Application readback evidence](alden-collection-application-readback-20261007.json) contains only aggregate results and artifact identity. This owned candidate capture does not attest the primary installed process, physical voice, shortcut, Retina, OS-lock, notarization or production. The installed app update remains a separate gate.

The unsigned release cache is now repository-specific and the Rust setup action's automatic cache is disabled for release jobs. The supported input was checked in the publisher's [action schema](https://github.com/actions-rust-lang/setup-rust-toolchain/blob/v1/action.yml). A successful new remote CI run is still required.

Remaining: source-order/deletion/revision reconciliation and processing-version policy; conflict/merge history; scheduler storage/execution and interruption recovery; host-specific skill discovery/execution; live Threads/YouTube acquisition scope; Dense integration; meaningful connected overview and graph source/filter navigation; actual-event3D; installed bundle, CI/release/production readback. No new unattended schedule or canonical source-store migration has been enabled.
