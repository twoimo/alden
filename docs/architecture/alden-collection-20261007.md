# Alden multi-target collection — current execution boundary

The collection journal retains independent source identity, immutable original snapshots, version history, target/project membership, per-target locking, transactional checkpoints and ordered persisted stage events. Failed batches roll back documents, indexing and checkpoints; failure history remains. Exact source replay does not create another version. Semantic similarity does not merge authors or entities.

Actual configured metadata sources were read: employment public graph, Spark privacy-filtered derived index, 장사의신 and YouTube 쯔양. 쯔양 is a distinct project from Tzudong. Their existing stores remain canonical; this is a separate logical derived projection.

[Full configured-scope readback](alden-collection-baseline-20261007.json) reports26,622 nodes,48,355 explicit source relations and zero in-scope orphans. One run per source imported407/1118/12561/12536 records and402/1116/22388/24449 relationships in.315/.965/22.285/65.263 seconds respectively. Source extraction/indexing only; these timings are not independent semantic review or a before/after speed claim. Stored stages confirm FTS indexing only; Dense and application visualization need separate receipts.

The common skill has one canonical user source with two discovery links. Its current adapter contracts consume declared Threads snapshots, existing YouTube/source graph JSON and the derived Spark SQLite index. SQLite Online Backup handles the live derived WAL index without changing its journal or forcing a checkpoint. Original protected files, credentials and message targets are unchanged.

Collection unit contracts:8 passed. Actual bounded replay of5 source records reports0 added,0 revised,5 unchanged; version count26622 remains26622. This is a bounded replay check, not a complete incremental-refresh benchmark.

An actual OSK receipt node was created and re-read in the verified Spark scope:261007-1dyd-bpdjy6m2. It preserves a derived-from link to the existing file graph source. This one receipt does not mean all26,622 records were transferred into OSK or the final logical graph contract is finished.

Remaining: source-order/deletion/revision reconciliation and processing-version policy; scheduler storage/execution and interruption recovery; host-specific skill discovery/execution; live Threads/YouTube acquisition scope; original-byte/source integrity validation; Dense integration and project/permission filters; bounded graph paging/focus API; collection-history UI and real-event3D; resource allowlist, installed bundle, CI/release/production readback. The current actual collection store is task-private integration-state, not the installed app's store. No new schedule or source-store migration has been enabled.
