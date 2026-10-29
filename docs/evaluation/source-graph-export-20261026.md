# Complete source-record graph export

The display graph and the initial GBrain adapter intentionally limit selected
nodes and edges. Reusing that response as a corpus export silently omits records
and cross-page relationships. `scripts/alden_collection_export.py` adds a
separate, explicit CLI export of all eligible current source records in named
projects, without display limits, resolution aliases, or degree sampling.
It is the source-export prerequisite for a full GBrain import; the existing
50-record managed GBrain reader is still bounded.

## Source and archive contract

`CollectionStore._graph_scope_sql` supplies the same membership, current-version,
permission, and relation-history eligibility to graph views and exports. The
scope is materialized before native source callbacks. An observed callback on an
unrequested project's native target was reproduced and fixed; that target's
data had not been returned. No new native-source permission is granted.

The original collection is opened read-only, with its existing query-only
transaction and source-authority checks. The output must be a new private
directory. A separate SQLite archive contains targets, source nodes, directed
relation groups and source contexts. Node IDs and versions remain original
source identities. Each group contains the full current claim count and the
canonical revision over every claim ID/version, with a bounded sample of 16
claim IDs. Claim counts do not mean independent corroboration. Full evidence
bodies and individual claims beyond the sample remain in the original store.

The archive omits `body` and `description` fields, but labels and source-context
labels can contain source text. It is private derived data, not an anonymized
artifact, a merged identity graph, an ontology-validation result, or fresh
permission to access an original. The open-ontologies reference informed the
scope/evidence distinction; no upstream ontology engine or formal checker runs.

Independent SQL counts, framed row hashes, foreign-key checks, and persisted
readback must pass before a complete receipt is written. Original authority is
checked again after archive verification and hashing. File identity and change
tokens bind the receipt to those verified bytes. Withheld native records,
unavailable authority, corrupt relation history, cancellation, or record/byte
budgets fail instead of producing a smaller successful export. Unpublished
output is removed only under its created directory identity and owner record;
replacement directories and symlinks are refused.

This is a consistent **as-of collection revision**. Ordinary content changes
after the read starts may leave a valid older snapshot, explicitly reported by
`checkpoint_unchanged_at_completion=false`. Permission or scope-stream changes
abort it. Source reads, archive writes, and authority observations are separate
operations, not an atomic cross-system transaction. A completed archive does
not automatically disappear after a later revocation.

`verify_export` checks artifact integrity only. The CLI `verify` uses
`verify_current_export`, which opens the exact bound source/project set, checks
the checkpoint, fully rescans current records and compares counts and row hashes.
That explicit full scan is not incremental validation. Consumers must revalidate
authority before using an archive; this CLI introduces no snapshot query API or
global MCP registration.

```sh
python3 scripts/alden_collection_export.py export \
  --state-root /absolute/existing/collection-root \
  --project exact-project --output /absolute/new/private-export
python3 scripts/alden_collection_export.py verify \
  --state-root /absolute/existing/collection-root \
  --project exact-project --output /absolute/new/private-export
```

Native Kakao sources additionally require the existing SDK authority factory via
`--messenger-source kakaotalk`; no SDK or authority is synthesized. Default
budgets are 2,000,000 target/node/claim records, 2 GiB archive, and a 300-second
CLI deadline. Exceeding a budget fails the export. SQLite materialization and
source guard caches still consume memory; no constant-memory or latency claim
is made.

## Observed validation

397 affected Python tests pass, including 20 exporter tests. Normal, valid-empty,
scope denial, original read-only ownership, withheld SDK records, history
tampering, mid-export permission/content changes, late permission revocation,
cancellation, byte/record/row budgets, changed archive bytes and replacement
directory behavior are covered. Both native packaging resource lists include
the standalone script.

Seven isolated mutants are detected: reintroduce a visual node cap, omit source
versions from relation proofs, invoke unscoped source callbacks, accept withheld
records as complete, skip final authority checks, skip persisted readback, and
ignore changed file tokens. The capped-node mutant trips the exact SQLite
endpoint constraint; the other six fail test assertions. These are local
regression controls, not independent review or a formal correctness proof.

An actual CLI replay uses the existing frozen MIRACL Korean dev judged-passage
pool: 2,835 passages, 213 query records, and all 3,057 official relevance
judgments, including grades 0 and 1. Directed `judged-for` edges represent those
human retrieval judgments, not factual/semantic relations or universal relevance.
The entire registered fixture exports **3,048 nodes / 3,057 groups and claims /
1 target**. Independent readback matches every expected source ID, raw-input
binding, endpoint pair and grade-bound relation-proof revision. The source
fixture and all input files retain their hashes. The archive is 6,955,008 bytes.
No downloads, embeddings, model calls, browser actions or native app launches
were used. The underlying 1,486,752-passage MIRACL corpus was not exported.

Receipts and reproducible task scripts live under
`outputs/graph-export-20261025T190849Z/`, particularly
`source-public-input-binding.json`, `source-public-result.json`,
`affected-tests.log`, and `negative-controls.json`. Public corpus text and the
archive are not committed or published by this change.

## Remaining gates

The full GBrain importer, incremental source-event scheduling and revocation
disposal are not connected to this exporter yet. Native/private representative
gold, installed GUI/runtime acceptance, release/deployment/rollback evidence and
the full original 18/follow-up/ST01–16/Cursor gates remain open. Alden stays off.
The requested web Astra/max independent worker is unavailable in exposed worker
metadata; no model is substituted. Current-source ChatGPT/Gemini/reverse review
remains NOT_RUN under inherited source-upload/readiness/coordinator holds.
