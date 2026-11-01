# Full source-record GBrain import

The original GBrain path reader imported a fixed 50-record display packet. The
new `alden_gbrain_import.py` consumes the complete authorized source archive in
a separate `alden-gbrain-full-cache-v1` root. The optional knowledge-MCP
`--gbrain-full-root` exposes paths over that full import. Existing bounded and
unmanaged configurations keep their formats; the root kinds cannot adopt one
another. No global host, source enrollment or installed desktop setting changes.

## Original authority and derived files

Every generation owns its source archive, SQLite graph index, keyless PGlite
store, home/configuration, audit files, operation record and acceptance receipts.
The existing manager supplies ownership checks, inherited process leases,
atomic active pointers and durable retirement records. Its kind is now a class
constant so full and bounded generations retain distinct deletion authority.
Changed or failed refreshes never restore withdrawn generations. `withdraw`
does not instantiate an original provider just to remove its owned cache.

The exporter and explicit current-source verifier retain the original read-only
collection/SDK contract. Source authority and complete source rows are checked
before import, around publication, and before/after reads. Index file identity
and change tokens cover source revalidation too: a reproduced late index change
could otherwise replace labels after the first hash check. A read never cleans
files or starts an importer. Current-source verification is still a full scan.
These filesystem, source and engine steps are not one atomic transaction or a
formal revocation/liveness proof.

The index preserves original IDs, selected source versions, labels, contexts and
directed relation-group proofs. GBrain pages commit a digest over each complete
exported node record, alongside its original ID/version/target. Link contexts
carry the full exported group proof. Current selected-version membership creates
real `sourced_from` edges and registered-origin nodes; it is not an entity merge
or independent corroboration. Record slugs remain stable across versions, while
each generation fingerprint binds scope, original root, checkpoint and full row
hashes. Incremental mutation of those slugs is not implemented yet.

Body/description fields are omitted, but titles and context labels can contain
source text. These are private derived files, not anonymized data or later
permission to read an original. Full claim counts and revisions are preserved;
individual claim IDs remain sampled at 16 as in the canonical source view, with
the remaining individual claims/evidence in the original store.

## Engine protocol and complete readback

The reviewed publisher pin remains GBrain v0.60.451.0,
`33d73cb54c14e2e988272764ddff59a5ead10915`. A private generation writer uses one
full-access stdio session for fixed page/link operations; the read-only client
and knowledge MCP gain no write tools. The runtime receipt, exact binary hash,
source guard, disabled embeddings and local configuration are checked. Provider
credentials are not inherited by the engine.

Pages are streamed in publisher-supported batches of 50. Every batch must be
terminal/committed with exact integer counts, ordered slugs, disabled embedding
state and opaque revision tokens. Keyset reads close their SQLite SELECT before
committing those acknowledgments. Every engine page is then enumerated in
100-row source-scoped pages and compared to the complete ordered index. Missing,
duplicate or extra engine pages fail publication. Explicit pagination continues
to the real end; `listing_truncated` is not accepted as a complete listing.

Every stored page is read back with its original metadata digest and exact
acknowledged revision. Its outgoing graph rows must match the complete SQL
expected set, including direction, depth, source IDs and edge proofs. Enumerating
all pages and verifying every outgoing set covers every stored link once. This
avoids forcing thousands of inbound source memberships into one graph response.
The earlier both-direction verifier failed at the pinned 5,000-row boundary for
a controlled origin with 5,103 source records; the outgoing verifier completes
all 5,104 pages and 5,105 links in that regression.

Actual whole-pool runs surfaced the publisher's `features_auto_fix/coaching`
notice. Inspection of the pinned `src/commands/features.ts` and onboarding code
confirms it is a recommendation, potentially including paid operations. The
client records that exact reviewed code/kind and ignores its proposed actions;
no fix, extraction, provider or model call is dispatched. Unknown/degraded or
unpaired notices still fail. Independent validation also discovered that the
read-only surface excludes admin `get_stats`; no access grant was added. Page
enumeration and exhaustive original link-set comparison supply the counts.

An unreaped writer leaves its unpublished generation for lease-protected
recovery. An exited failed worker's generation is removed. No failed batch is
silently replayed under another request ID in the same store. After a discarded,
terminal generation, a corrected refresh builds a genuinely new private store.
Private source exception text is not converted into public CLI status codes.

## Use and bounds

```sh
python3 scripts/alden_gbrain_import.py create --root /abs/new/full-cache \
  --state-root /abs/existing/collection --project exact-project \
  --runtime-root /abs/reviewed-runtime-root \
  --runtime-home /abs/reviewed-runtime-root/home \
  --runtime-receipt /abs/reviewed-runtime-root/runtime-receipt.json
python3 scripts/alden_gbrain_import.py refresh --root /abs/new/full-cache \
  --state-root /abs/existing/collection --project exact-project
python3 scripts/alden_knowledge_mcp.py --state-root /abs/existing/collection \
  --allow-project exact-project --gbrain-full-root /abs/new/full-cache
```

Native Kakao originals additionally use the existing explicit
`--messenger-source kakaotalk` factory. Full-root queries require the root's exact
project set; no ambient or all-project expansion occurs. The standalone CLI also
has `query --focus ORIGINAL_ID --depth 1 --direction out` and `withdraw`.

This is a full rebuild, not a source-event/delta importer. An unchanged checkpoint
performs no engine imports, but still exports/rechecks/indexes a temporary probe
and removes it. Default limits remain 2,000,000 target/node/claim source records,
2 GiB source archive, 4,000,000 derived page/link records and a 2 GiB graph index;
the CLI deadline defaults to 1,800 seconds. Exceeding a limit fails the whole
generation. The engine's 5,000-row/8 MiB graph transport and the MCP response
budget still bound individual path queries, and a single source record with
extreme outgoing fan-out can exceed verification transport. No truncated
response becomes a smaller successful import/query. No constant-memory,
latency/token/quota saving or unbounded-corpus claim is made.

## Observed evidence

425 affected Python tests pass, including 26 full-import tests. They cover
complete/empty sources, over-2,048 node streams, the 5,103-record source-origin
case, exact proofs/revisions/page enumeration, wrong scope, source/permission
changes during build/read/publication, cancellation, late index changes,
withheld SDK records, separate root kinds, private failures, read-only MCP
routing and unreaped-worker recovery. Eleven isolated source mutants detect
lost batches/groups/record proofs, skipped authority/file/revision checks,
retained failed data, premature orphan deletion, omitted page enumeration and
the large-origin traversal regression. These are local controls, not an
independent worker review or formal proof.

Actual pinned-engine replay uses the frozen public MIRACL Korean judged pool:
2,835 passages + 213 queries = 3,048 source nodes; 3,057 official grade-0/1
judgments are explicit `judged-for` records. Import stores **3,049 pages and
6,105 links**, including one actual registered origin and 3,048 memberships.
All pages/revisions and outgoing sets are checked. A separate read-only engine
session enumerates every expected slug, checks every judgment/proof from all
213 query anchors against the previously frozen source-verified archive, and
checks all 3,048 memberships. It does not use the newly built index as its
expected oracle. The full original 1,486,752-passage MIRACL corpus is not tested.
Judgments are retrieval labels, not factual/semantic relations or private gold.

An unchanged refresh keeps the generation. A valid full-root MCP path succeeds;
denied-source and withdrawn-cache paths fail with clean exits. Controlled full
scope denial removes the active pointer and every generated directory, including
the source archive/index/engine files. The original frozen fixture and inputs
remain exact. No app GUI, original operational writer, new download, embedding,
provider/model change or installation is used. Failed notice diagnostics and the
unsupported admin-read assumption remain recorded separately from successful
readback; the accepted original operation is reconciled rather than reimported.

Source evidence lives under `outputs/gbrain-full-import-20261025T195140Z/`.
The final source/packaged receipts record exact module hashes and scope; corpus
text and private runtime files are not committed. Source-event invalidation,
journal/delta recovery, private/native representative gold, independent-host
acceptance, native installed GUI and all original 18/follow-up/ST01–16/Cursor
release gates remain open. Alden stays off. The web-Astra/max worker is not
exposed and is not substituted. Current-source ChatGPT/Gemini/reverse review
remains NOT_RUN under inherited source-upload/readiness/coordinator holds.
