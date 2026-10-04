# Alden canonical OSK migration

The product engine remains OSK v4.1.2, commit `9bbf08febc5a1fb2af068006735ed79cbdb71178`, with its existing documented adjacency performance patch. The separate session connector is not the product vault. See the pinned [Constitution](https://github.com/lpaiu-cs/osk-system/blob/9bbf08febc5a1fb2af068006735ed79cbdb71178/_governance/Constitution.md), Bylaws and Mechanism in the same repository.

The old product classified text with fixed lexical dictionaries, promoted assignments into topic entities, created room assignments, and copied cached ERE relationships into both endpoints' body Links. This could make an imported relationship look like a reviewed OSK dependency.

Python and Rust no longer classify messages into topics, promote topic entities, filter retrieval by those tags, or award topic-specific retrieval bonuses. The room-title resolver no longer uses dominant topic labels. Compatibility arguments and tables remain inert. BM25, dense retrieval, source identity, original roles and room boundaries are retained.

The graph now reads parsed canonical notes through OSK's resolver. Body Links are directed dependencies; `derived-from` and `conflicts` remain separate predicates. Raw and other non-node coordinates are provenance only. Membership uses stored paths and directly authored links to real hubs. Room IDs, neighboring directories and connected components cannot synthesize a cluster. Raw re-import cannot overwrite notes whose automatic claims the user expressly withdrew.

## Measured private migration

| Store | Archived topic notes | Rewritten notes | Classification projection rows removed | Topic entities removed | Related ERE rows removed | Dense/ANN rows removed | Pack tags cleared |
|---|---:|---:|---:|---:|---:|---:|---:|
| auto-reply | 0 | 5 | 40,297 | 13 | 358 | 0 | 2,648 |
| bujamentor | 18 | 126 | 93,628 | 15 | 341 | 135 | 0 |

These are counts from the actual local stores, not performance estimates. Table projection counts include both assignments and aggregate rows; dense/ANN counts combine vector rows and bucket memberships. Archived note counts differ from active entity counts because three topic notes were already retracted.

The bujamentor migration retained all **5,639 immutable Raw source files**, checked by complete before/after SHA-256 manifests. Its migration and readback took 48.550 seconds, n=1, including validation and the second Raw hash scan. This is not a steady-state indexing benchmark or an improvement percentage. No Kakao original database journal mode or checkpoint was changed.

Final bujamentor readback contained 142 nodes and 141 actual body Links, all navigation; no cached ERE semantic edge remained. This does not establish new semantic knowledge: meaningful dependencies require source-grounded OSK distillation and organizational review. Removing unsupported relations intentionally reduces apparent connectivity. The adapter still retains imported source metadata notes; full semantic distillation is a separate unfinished goal requirement.

## Recovery and verification

Each original node byte is copied under private `_archive/alden-preclassification-v1/` before mutation. The complete change manifest is prepared first; OSK's shared mutation lock, CAS body writer and `_ledger/migration/events.jsonl` record the changes. Incoming references to retired topics become plain text, preserving display labels and surrounding prose. Pinned/protected or independently edited topic notes are held and reported. The actual two stores had no such holds. The migration does not approve protected content.

Private recovery includes SQLite Online Backup snapshots of affected Alden indexes plus the complete pre-migration notes and checkpoint. Old topic IDs remain tombstoned in the checkpoint, while ordinary topic files are removed from the live node space. Direct topic and OSK ID lookup is rejected. Database triggers prevent already-running legacy writers from restoring lexical projections; source messages and operational queues remain intact.

Focused regression covers the real bundled OSK writer/resolver, explicit direction, Raw non-node references, backup integrity, replay, ID lookup, manual insertion and preservation, source deletion, ambiguous IDs, and database/FTS/dense retirement. Python tests: 34 OSK integration, 102 graph/retrieval, 27 reference-store. Additional affected checks passed: 69 corpus/source/reference-search tests, 63 Rust context tests, 6 Rust corpus tests, and 355 frontend tests. Mandatory CI and installed-bundle readback are independent delivery checks. UI rendering, installed version and production signing are independent evidence states.

Private chat contents, source paths and screenshots are excluded from this public document. Developer ID signing/notarization, physical voice/shortcut/lock verification, Flash-Next memory admission, Dot's blocked network environment, whole-app performance comparison and full semantic distillation remain open goal requirements.
