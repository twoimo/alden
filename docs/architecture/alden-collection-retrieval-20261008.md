# Retained collection text, local hybrid retrieval and MCP — 0.3.35

Collected documents were visible in history and the graph but were disconnected from local voice retrieval and the metadata-only MCP. The career graph adapter had also retained complete source records while leaving text fields empty: it read `label`/`summary`, although many archived records contain `localOriginalText`.

The new disposable projection reads that exact retained field, identifies its origin, and preserves the source author's handle/date/URL/text-origin metadata when available. Source versions, original bytes and record hashes are unchanged. Three different states remain separate: an empty old projection, archived source text, and a genuinely empty source. No title or body is invented to make embedding succeed.

The explicit producer indexes every current authorized version needed by each selected project. One project's newer version cannot replace another project's retained older version. Complete vectors are committed incrementally under one producer lock and can be reused after interruption. The local encoder remains the already-loaded pinned E5 service. User queries never embed documents or load a model. They use BM25 and full eligible-scope Dense/RRF only when text extraction, model, endpoint, algorithm and version stamps all match; incomplete or stale coverage remains `bm25_only`.

Queries require explicit projects and recheck retained hashes/versions before citation. Collection-time filters are applied before lexical candidate limits. The API calls these collection timestamps rather than source publication times. Exact stored relation direction/type and target membership versions are preserved. Local project retrieval cannot be combined with a Kakao room or participant scope. Explicit voice project names can select the collection route; ordinary conversation retains its existing path and model selection.

`alden_knowledge_mcp.py` reuses the existing bounded stdio transport. Startup requires an absolute state root and `--allow-project`; tool requests can only use subsets of that allowlist. It exposes read-only search, bounded graph and history tools, and observes cancellation/deadlines without latching the global abort file. The original status MCP's default catalog and metadata behavior remain unchanged. Both installed executors must be run with the pinned Python and `-B` to preserve the app signature.

```sh
"<pinned-python>" -B /Applications/Alden.app/Contents/Resources/scripts/alden_collect.py \
  --state-root "<existing-private-state-root>" --index-dense --project career
"<pinned-python>" -B /Applications/Alden.app/Contents/Resources/scripts/alden_knowledge_mcp.py \
  --state-root "<existing-private-state-root>" --allow-project career
```

These commands do not install a connector, change host permissions, add a schedule, acquire a live social account, or authorize publication/messages. Host-specific skill discovery and execution remain separate checks.

## Actual candidate evidence

An Online Backup snapshot and integrity check preceded the new projection. All original counts remained unchanged: 4 targets, 26,622 documents/versions/memberships, 48,355 relations, 34 runs and 133,115 persisted stages.

The first real attempt rejected an empty embedding input before storing any vector. A shallow projection then indexed 14,213 entries; inspecting retained records revealed 12,409 old empty text projections. After using their archived text field, the real pinned E5 producer covered 26,162 searchable versions and retained 460 genuinely empty entries. It changed 12,026 vectors across 23,933 windows, reusing prior valid vectors. Final JSON reported `ready`; the process was observed live and then terminal. Its PTY exit status was not retained, so no exit-zero claim is made for that final producer. No source count or history was changed by cache construction.

The actual stdio client used the pinned menubar runtime, initialized the candidate server, listed three tools and rejected a `jangsin` query under a `career`-only startup allowlist. The career query returned `rrf` over 12,101 searchable documents in 2.8715 seconds (n=1). Its entity ID, source version and SHA matched graph focus/detail and the five persisted discovery/parsing/validation/storage/FTS stages. This is a direct candidate MCP and retrieval readback, not Aside/ChatGPT host execution, a new source-change event, physical primary-window verification, latency percentiles or a measured speedup.

The affected Python suite passes 111 cases, including 12 new collection retrieval cases; Rust desktop108 and Clippy pass. Formatting-only audit-source reflows satisfy the full desktop formatter.

## Installed delivery

The first CI run found two missing modules in the automatic-reply session package's import closure. Adding the two required data/retrieval modules fixed three packaging failures; 38 affected packaging/retrieval cases pass. Runtime source `9a2e203a21b6e04a2a9af3a7483acb04bb52f6a8` then passed all four CI jobs and a fresh canonical bundle build.

The normal installer delivered0.3.35. All49 files equal the candidate and strict/deep ad-hoc signature passes. Three settings remain identical to pre-cutover backups. The exact prior0.3.34 app was separately copied before cutover and all47 hashes still match; recovery no longer assumes the installer retains an app archive. Primary PID97242 is managed and running; model63062 and embedding11016 remain alive.

The installed MCP server passed the same owned initialization, allowlist rejection, actual RRF search, graph/detail and persisted-history readback. Its one career query took2.9410s over12,101 searchable documents (n=1). This is a separate sample, not a paired performance comparison with the candidate. The installed voice reference adapter separately returned RRF with two retained sources and3,783 reference characters, preserving project, source version, field origin and author. It did not open a microphone, generate a reply, play TTS or write user history.

The shared collection skill was updated at its single canonical Aside source, with both discovery links and file hashes read back. It documents pinned Python with `-B`, explicit local indexing/project queries and the collection MCP's actual scope. Link/hash readback is not host-app discovery or execution proof. Original project stores, graphs, collection counts, queues, settings and model profiles remain preserved.

The full goal remains active: actual two-host skills and shared OSK chain, live acquisition, scheduler, deletion/order/conflict/processing-version policy, independent retrieval-quality evaluation, physical voice/global shortcut/primary UI, Flash effective memory/provenance, DPO, signed release and production.
