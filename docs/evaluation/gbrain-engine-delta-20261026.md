# Selective GBrain engine updates

The full importer previously rebuilt every page/link after a source change.
Its page frontmatter and link context also contained the whole-snapshot digest,
so one changed record changed every engine payload. Opt-in `--incremental` now
uses the `alden-gbrain-record-content-v2` projection: original IDs, versions,
complete record digests and relation proofs remain in engine rows, while the
current whole-source fingerprint remains in the archive/index/acceptance and
query result. Unchanged records no longer acquire a different engine payload
just because another record changed.

Legacy `alden-gbrain-full-source-v1` generations remain readable. The first
incremental refresh of a legacy generation builds a fresh stable-format store;
it does not reinterpret the old page format. Subsequent ordinary full rebuilds
preserve the generation's selected format. Unknown policies fail. The existing
full-cache root/source/project ownership contract remains unchanged.

## Mutation and acceptance

Under the exclusive inherited cache lease, the updater validates the accepted
base archive/index and exact keyless runtime configuration. Source root,
projects, authority, file identity, journal stream and forward cursor must
remain compatible; otherwise it uses an independent full rebuild. The live
consumer still purges a known authority change before exporting survivors.
Explicit `rebuild=True` never reuses a base engine.

Every earlier engine session has closed before that lease becomes available.
The updater copies that closed generation's engine and home into an unpublished
generation, checks copied bytes and source file tokens, and rewrites only the
known `database_path` configuration field. A database path pointing elsewhere
is rejected. Symlinks, special/hard-linked files and wrong owners are rejected.
Copy limits are 100,000 files and 8 GiB; exceeding either fails instead of
silently publishing less data. No original source journal/checkpoint is changed.

SQLite joins compare the complete old/new page and link indexes. Unchanged
pages retain their accepted opaque revisions. Changed/new pages use publisher
50-page batches; replacements carry the observed revision. Links with changed
contexts are explicitly removed before replacement. A removed page is purged
through the publisher's trusted-local `delete --purge --expected-revision`
operation, which removes incident links too. MCP does not receive a purge grant;
the private writer alone adds the existing `remove_link` operation. Exact
terminal batch counts, integer unlink counts and purge slug/source/status are
checked. No uncertain mutation is replayed in the same store under another ID.

Before accepting a generation, the same complete engine verifier enumerates
every page, checks every page's source metadata and opaque revision, and
compares all outgoing edge/proof sets. This includes untouched pages. The
temporary base index is removed before publication; `delta.json` records base
fingerprint and actual engine write counts. Source-current checks still run
around publication and on reads. Changed/failed data never rolls back to an
older generation. An unreaped child retains its unpublished generation and
inherited lease for existing recovery, rather than having its files deleted.

A process crash after engine mutation but before publication leaves no active
pointer. A new owner discards the unpublished generation and fully rebuilds
from the currently authorized source. This is safe restart reconciliation,
not resumption of a partially acknowledged engine batch. These source,
filesystem and engine transitions are not a single atomic transaction or a
formal liveness/revocation proof.

## Use

Create a reviewed full cache as documented in `gbrain-full-import-20261026.md`.
The exact existing source/project binding is then used for either entry point:

```sh
python3 scripts/alden_gbrain_import.py refresh --incremental \
  --root /abs/existing/full-cache --state-root /abs/existing/collection \
  --project exact-project
python3 scripts/alden_gbrain_watch.py run --incremental \
  --root /abs/existing/full-cache --state-root /abs/existing/collection \
  --project exact-project
```

Native Kakao originals retain the explicit `--messenger-source kakaotalk`
factory and its current-source access checks. No original collector/service,
global scheduler, host registration or desktop setting is modified here.

## Observed evidence

The companion JSON records the current related Python tests, isolated mutants
and exact source/runtime receipts. Tests cover legacy migration, unchanged
refreshes, additions/revisions/retractions/relations, empty graphs, batches
crossing 50 pages, a 2,104-page stable generation, revision-bound requests,
unchanged-page corruption, journal rewind, file replacement, authority changes,
copy/config safety, abort, failed receipts and unreaped-worker recovery. The
large synthetic generation changes one page and retains the other 2,103
revisions; it is an algorithmic regression, not a large actual-engine benchmark.

Actual pinned-engine replay uses a copy of the frozen public reference fixture
and a separate source writer. The initial six pages/five links are fully
verified. A version change writes one page and removes/adds its membership
link; the other five page revisions remain exact. A new record writes one page
and one membership, retraction purges one page, and a removed relation unlinks
one edge without writing pages. Both a crash after cache acceptance (exit 23)
and a crash after delta mutation but before publication (exit 24) recover from
their actual persisted states. Manual withdrawal stops the consumer; partial
and full declared denial purge old data, with zero final generations/pointers.
Five MCP calls accept three current paths and reject two withdrawn paths, all
with clean exits. The original public fixture remains exact. These are copied
public-source controls, not actual publisher/native-account revocations.

An initial broad test command accidentally used the system Python 3.15 instead
of the project's pinned 3.11. One existing collection query exceeded that
SQLite build's nesting limit. The same single test fails on the unchanged
preceding a090b07 source under that system runtime; the pinned-runtime related
suite passes. The collection query was not changed or the failure suppressed.
This does not establish Python 3.15 support.

Evidence lives under `outputs/gbrain-engine-delta-20261025T215737Z/`. Source and
package/CI evidence are separate; source acceptance does not imply installation.
The reviewed GBrain/open-ontologies pins remain those in
`gbrain-scoped-paths-20261026.md`. No external ontology checker is run.

Only engine mutations are selective. Source export, current-source/index
verification, disk joins, engine copying/hashing and complete engine readback
still cover the full store. Engine write counts do not prove end-to-end
O(delta), latency, memory, token or quota savings. Historical engine metadata,
audit or storage blocks are not claimed to be forensically erased. Native
collector/event integration, source-side incremental indexing, private/native
representative gold, independent-host acceptance, external ChatGPT/Gemini/reverse
and required web-Astra/max worker validation, installed GUI and normal release
remain open under the existing holds. Alden stays off; the complete original
18/follow-up/ST01–16/Cursor objective remains active.
