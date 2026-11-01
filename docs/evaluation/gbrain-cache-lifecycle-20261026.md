# Managed GBrain cache generations

The scoped path reader rejected stale results, but provisioning and disposal of
its derived files were manual. `alden_gbrain_cache.py` adds a separate manager
for a private root bound to one original collection-root identity and explicit
project set. It creates, validates, publishes and withdraws disposable GBrain
generations. Read-only MCP gains an optional `--gbrain-managed-root`; it cannot
invoke the manager's write operations or widen the configured scope.

This is a lifecycle candidate for the existing fixed 50-record graph query.
Whole-corpus pagination/delta import and automatic source-event scheduling remain
unfinished requirements. A changed snapshot rebuilds its bounded generation;
this is not an incremental whole-corpus performance claim.

## State and authority

| Condition | Manager result | Reader result |
|---|---|---|
| No generation | Build a fresh private DB/home/audit directory | Unavailable until publication |
| Same authoritative snapshot | Keep the generation; perform no engine writes | Recheck current source authority before/after the read |
| Changed version, membership or explicit relation | Retire the old generation before building a replacement | Old snapshot fails current binding |
| A permitted project has no selected records | Publish a validated empty graph with its real scope/revision | Unknown focus is unavailable |
| All targets in a formerly bound project are denied | Remove generated files and return `gbrain_cache_scope_withdrawn` | No old cache is served |
| Original store is no longer configured | Withdraw generated files; return an error | Unavailable |
| Other original read/permission/IO failure | Propagate failure; do not manufacture an empty snapshot or grant | Fail before the engine read |
| Build/cancellation/source-change failure | Remove the unpublished generation; never restore withdrawn data | Unavailable |
| Interrupted removal | Complete removal from the external retirement record | Active pointer was invalidated first |

The original collection API had two different empty/failure cases. A project
with no permitted targets raises `collection_project_scope_invalid`. A valid
empty selection returned no scope, targets or revision. The manager classifies
the former as withdrawal; `alden_collection.py` now preserves scope, targets and
the real scoped revision for the latter. Read-only database transactions retain
their existing `BEGIN`/query-only/current-authority checks.

## Process and file lifetime

Readers hold a shared lease; management holds an exclusive lease. Both CLI and
stdio children inherit the actual lease descriptor. Closing a parent's descriptor
does not explicitly unlock it, so an orphan child still using a DB keeps that
generation protected. The root and lease inode, ownership and private modes are
checked. A live child/lease is not evidence of a completed operation or permission
to delete its store.

Each generation owns its PGlite files, `GBRAIN_HOME`, audit files, operation
receipt and graph manifest. Old directories are unlinked, including those
artifacts. A small retirement record outside the directory retains deletion
authority if a crash already removed the generation marker. Unknown directories
or symlinks cause refusal; they are never followed or removed. No original store,
global configuration or another generation owner is a deletion target.

The publisher runtime pin is checked before even initializing a new store and
again against its resulting keyless configuration before labels are imported.
Bulk writes require committed terminal counts, the exact requested page list and
disabled embeddings. Independent reads check canonical top-level titles, opaque
revision tokens matching the write acknowledgments, original source IDs/versions
and graph fingerprints. All generated nodes receive a depth-one graph query and
exact edge/context/depth validation. Current original authority is read again
before and after the atomic active-pointer publication.

These are separate original DB observations, engine operations and filesystem
steps, not a transaction spanning those systems. Readers always recheck current
authority. After a crash, an unaccepted private store can be discarded; an already
published matching pointer is read back without replaying imports. No automatic
rollback to a withdrawn source version occurs. `withdraw` does not reopen the
original provider merely to remove this bound owned cache.

## Observed verification

288 affected Python tests pass, including 20 lifecycle tests. Actual subprocess
tests cover a child retaining its reader lease after its parent exits. Controlled
tests cover original source errors, valid empty scope, full denial, source change
during build/publication/no-op, cancellation, unknown symlinks, changed runtime
bytes, bounded manifest reads, and recovery after removal lost its marker.

Six isolated mutations are detected: retain deleted files, publish after source
change, retain a denied scope, drop child lease inheritance, drop empty-snapshot
scope metadata, and drop the external deletion-authority record. They are
regression controls, not a theorem or an independent review.

The exact source hashes in `replay-bounded-final/source-runtime-pin.json` were
checked at every CLI phase of an actual engine replay over a copied public
reference fixture. Initial publication persisted 6 metadata pages/5 edges and
verified all 6 nodes; unchanged refresh did no imports. A controlled original
version change published 6/5 again; one denied publisher reduced it to 3/2.
All remaining targets denied caused an error and left zero generation
directories and no active pointer. Three old generation directories were removed.
The withdrawn controlled label was absent from the managed root's remaining
files. Managed MCP reads passed current binding and rejected stale/denied focus
requests. The original public fixture was unchanged; controlled version and
permission changes touched only its separate task-owned copy.

Earlier diagnostic imports rejected incorrect assumptions about title location
and revision type. Their CLI calls terminated, never published a pointer, and
their temporary stores were removed before fresh corrected imports. The pinned
upstream exposes `title` at page level and an opaque string `revision`; the
accepted path compares that token to the write acknowledgment. These failures
and corrections remain in the local evidence directory.

## Limits and remaining work

Source/receipts: `outputs/gbrain-lifecycle-20261025T175947Z/`, especially
`replay-bounded-final/actual-lifecycle-acceptance.json`,
`affected-tests-bounded-final.log`, and `negative-controls.json`.
This replay is a controlled public fixture, not native messenger permission
evidence, a publisher's actual update, or private representative semantic gold.
Directory removal and a literal-label check do not prove forensic erasure from
filesystem snapshots, backups or free blocks.

The manager owns only roots created for this contract. Existing unmanaged
reference stores are preserved; there is no implicit migration or global
registration. Freshness/authorization failures during read-only MCP do not turn
it into a cleanup writer. Automatic native-source invalidation, whole-corpus
incremental import, private/native UI acceptance and independent host/release
gates remain open. Installed Alden stays off. The full original 18/follow-ups/
ST01–16/Cursor goal remains active. Current-source ChatGPT/Gemini/reverse review
is NOT_RUN under inherited source-upload/readiness/coordinator holds; local
checks do not substitute for it or for formal correctness/liveness proofs.
