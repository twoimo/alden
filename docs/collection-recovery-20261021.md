# Independent recovery of the current collection

The installed Alden 0.3.208 runtime at `99b78015830a562421566ca72abf7f049038e2be` successfully read an independent restored collection, including its archived source history. This exercise covers collection recovery and read-only application transport. It does not establish a whole-app rollback, activate a restored scheduler, or resolve semantic correctness and retention policy.

## Snapshot and independent restore

Four live databases were backed up with SQLite's online backup API from pinned read transactions. The existing scheduler cycle lease was held for 3.1357 seconds while pinning and backing up the databases; the scheduler was not stopped and its persisted pause state was not changed. Each database had a bounded backup deadline. This is a read snapshot per database, not an atomic transaction spanning four databases. Normal derived WAL/SHM read metadata is outside any live-file byte identity claim.

Every table, including FTS shadow tables and stored vectors, was compared by complete typed row hashes and schema hashes against its pinned source snapshot. Every retained content-addressed source and capture file was copied and hashed. A second restore used independent file copies rather than links.

| Database | Tables compared | Snapshot bytes |
| --- | ---: | ---: |
| Collection | 26 | 822,349,824 |
| Scheduler | 6 | 6,914,048 |
| Realtime | 3 | 20,480 |
| Retrieval | 8 | 292,417,536 |

All 30,037 restored files matched their snapshot hashes and had distinct inodes. All 43 table row/schema comparisons and four database `quick_check` results passed. The collection retained 26,623 documents, 64,779 versions, 26,633 memberships, 48,355 relations, 118,555 relation versions, 15 targets and 470,954 journal events. No foreign-key violation was found. No resolution-history tables exist in this store, so this exercise cannot claim to have restored actual merge/undo history.

## Source and cross-file integrity

Reachable source captures were traced from target cursors, stored journal events and run cursors. All 1,477 reachable capture manifests and their archive hashes/lengths matched. Each of 92,368 record references resolved its exact retained JSON pointer, record hash, version and document; 73,347 relation-version references also matched. Missing reachable versions and current-version, membership and relationship-endpoint violations were zero.

Scheduler and realtime target references were valid, including durable manual-request-to-attempt references. The retrieval cache retained 64,779 text rows and 63,859 vector rows; all referenced actual retained versions. These are referential checks, not verification of embedding semantics or factual truth. Extra immutable files copied after the pinned database snapshot were retained without treating them as active records.

## Fresh installed MCP processes

The actual installed MCP stdio entry point was launched separately over the snapshot and the independent restore. Python audit hooks rejected file access outside the supplied data/runtime roots, network access and subprocess creation. Descriptor-relative opens were checked against their actual macOS directory descriptor paths. Six negative guard controls rejected absolute and descriptor-relative escapes, symlink escapes, an outside database, network access and subprocess creation. These Python-level guards are an observation aid, not an OS sandbox.

Both children exposed the expected read-only catalog. For each of five allowed project scopes, bounded graph, history and future-cursor reset responses matched exactly between copies. An unallowed project and a nonexistent write-like tool were rejected with `-32602`. Each child completed 17 tool calls and exited normally. No denied operation occurred during either application session. All 30,037 restored file hashes remained unchanged after reading.

The first probe failed because its guard interpreted a legitimate descriptor-relative abort-state read as a path relative to the shell's working directory. A subsequent harness assertion incorrectly expected a nested history checkpoint; the actual contract exposes `cursor` directly. Both failures and diagnostics were retained. Only the private verification harness changed; the installed implementation and its guards were not relaxed. Final runs used the corrected harness, including the negative controls.

Five affected installed module hashes still match the source revision, and the installed strict/deep signature verifies. No rebuild, reinstall, primary data swap, new embedding, generation request or restored writer activation was performed.

## Delivery and remaining gates

Private evidence is bound by `outputs/collection-restore-208-20261021/delivery-manifest.json`: database snapshot receipts, all-file/table restore comparison, source-pointer/cross-file checks, both MCP protocols, guard controls, retained failed observations and installed-runtime readback. Source archives, databases, source URLs, captions and private protocols are not published with this report.

Read-only collection recovery now has actual current-data evidence. Primary promotion, scheduler recovery under writes, whole-app/OS recovery, merge truth and rollback, retention/deletion, independent semantic gold, global multi-host wiring and normal signed release remain separate gates. The approved terminal204 research retains its exact request and local-priority approval but remains unsent under the shared model-allowance hold; this recovery exercise does not replace the required ChatGPT → Gemini → ChatGPT audit. The complete 18-item objective, ST01–13 and Cursor gates remain active.
