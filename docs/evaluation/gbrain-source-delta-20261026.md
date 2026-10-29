# Journal-assisted source and index updates

`refresh --incremental` and `watch run --incremental` now update the source
archive and graph index as well as engine rows. This retains the same explicit
source/project ownership, original query-only access, legacy generation reader
and independently confirmed engine-publication contract. It creates no service,
original database writer, global registration or desktop launch.

## Source archive

The previous accepted archive must pass its complete integrity checks and match
the exact original root/projects. Current authority, file identity, journal
stream and forward cursor must be compatible. A read-only SQLite backup copies
that derived archive into a newly owned unpublished output; original source
files are never checkpointed, switched to another journal mode or opened for
writing. Base-file tokens are checked around copying and final acceptance.

A scoped completed-run window identifies stored/published document IDs and
affected relation groups. Inactive relation history and old incident groups
cover removals; all current claims of a selected group are aggregated, including
claims held by another permitted target. Merely selecting one claim does not
shrink the group's count or digest. Unchanged membership events also matter:
another target can acquire the same current version without changing its body.
Publisher rows are refreshed independently of document events.

Only changed source payloads are upserted; missing affected groups are removed
before missing nodes. Deferred changed records/relations require their explicit
published receipt. A missing source-authority grant or OS denial never selects
another reader. Source/factory, budget, cancellation and uncertain save failures
do not become an empty successful archive.

The journal is not trusted as a completeness certificate. Every canonical
source row is compared against the resulting archive in the same source
snapshot. A stable untracked change rejects that candidate, removes only its
owned output, and uses a full export through the **same** source factory. This
also supports an older running collector that has not loaded the new journal
event. File/authority/stream changes or rewind need a fresh baseline. Limits of
100,000 window events and 20,000 document/group candidates select a complete
baseline instead of truncated success. Oversized/malformed event details fail.
Fresh source probes and archive integrity/hashes are checked before the receipt.

## Collector fixes found by this path

Ordinary relation-only ingestion previously wrote no stored change event.
`CollectionStore.ingest` now emits one `relations_changed` event only when
actual relation records change; deferred publication is preserved.

Retracting one target's membership previously left its incident claims active.
If another target still held that document, export could fail as withheld
instead of retaining the surviving source. Retraction now deactivates only the
retracting target's incident relations. Other targets' memberships/claims and
all relation-version history stay intact. Both regressions fail against the
preceding fe3f9fc collection implementation in isolated copies and pass now.
No original running collector is restarted or rewritten by the validation.

## Graph index and engine

A compatible stable-format index is copied from the accepted generation.
Canonical current pages/links are regenerated for comparison; conditional
upserts retain unchanged rows and opaque revisions. Temporary seen-key tables
identify stale links/pages, pruned in that order. Complete row/count/file-budget
checks remain. `index_updates` counts logical main-table changes, not temporary
table writes or every byte copied. An incompatible/legacy baseline builds a
fresh index. Schema packs are refreshed and validated before a copied engine
writer admits newly observed relation types.

The existing engine delta still uses revision-bound page batches, explicit
unlink and trusted-local purge, followed by complete page enumeration,
metadata/revision and outgoing-proof readback. Source-current checks surround
publication and reads. Failures never restore an older generation; unreaped
workers retain their inherited lease and unpublished files. These separate
systems are not one atomic transaction or a formal liveness/revocation proof.

## Evidence

425 related pinned-CPython3.11 tests pass, including 33 source-delta tests and
32 engine-delta tests. Another 342 messenger/relation consumer tests pass in
16 fresh processes. The source suite compares archives to independent fresh
full exports/current-source validation and covers shared origins, full
relation groups, additions/revisions/removals, unlogged changes, publication
release, scope/stream/replacement, budgets, malformed input, abort and uncertain
receipts. Existing canonical graph-view controls remain in the exporter suite.
Ten isolated code mutants fail the relevant assertions, including missing full
comparison, incomplete groups, deferred-source consumption, missing journal
events, retained claims/index rows and unconditional index rewrites.

Actual pinned GBrain replay uses a copy of the frozen public reference source
and a separate writer. The initial six pages/five links retain the preceding
version/add/retract/crash paths. One version update selects one source node,
changes one archive node and one index page/membership, writes one engine page
and retains the other five page revisions. A shared controlled document gains
two explicit memberships; retracting one leaves the other current origin and
removes only that owner's relationship. An actual MCP read confirms the one
remaining source context. A relation-only run journals and inserts its new
relation type without writing pages. An unlogged SQL relation removal triggers
the complete source baseline and then removes one index/engine link.

Eight recorded trials include actual process exits 23 after cache acceptance
and 24 after engine mutation before publication. Recovery discards unpublished
work and rebuilds current source rather than replaying an uncertain engine
mutation. Six MCP calls accept four current paths and reject two withdrawn
paths, with clean exits. Manual withdrawal stops the consumer; partial/full
declared denial leaves zero final generations/pointers and removes withdrawn
owned files. Original frozen input hashes remain exact. These are controlled
public-copy source/policy events, not actual publisher/native-account changes.

Source evidence is under `outputs/gbrain-source-delta-20261025T223243Z/` and the
JSON companion binds exact source/module/test/receipt hashes. Package/CI and
installed acceptance are separate stages. GBrain/open-ontologies pins remain
those in `gbrain-scoped-paths-20261026.md`; no ontology checker is run.

## Remaining bounds

Selective archive/index/engine row mutations do **not** establish end-to-end
O(delta). Full source comparisons/authorization, index regeneration, temporary
seen keys, copies, hashes and complete engine readback still cover the store.
No latency, memory, token or quota improvement is inferred from write counts.
Logical retraction does not claim forensic removal of old SQLite/PGlite pages,
audit/history or backups. Known scope changes still use pre-export withdrawal
and fresh stores. Native event/collector installation, private/native gold,
independent-host/resource acceptance, formal verification, external
ChatGPT/Gemini/reverse and required unavailable web-Astra/max worker, installed
GUI and normal release remain open. Alden stays off; the full original
18/follow-up/ST01–16/Cursor objective remains active.
