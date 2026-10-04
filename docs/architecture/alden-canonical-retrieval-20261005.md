# Canonical OSK retrieval

Alden uses the bundled OSK v4.1.2 SDK at commit `9bbf08febc5a1fb2af068006735ed79cbdb71178` to read actual canonical notes. This is distinct from the session's separately installed OSK connector. The graph reader and retrieval now share the note IDs, bodies and persisted references. Governance, workbench, archives, hubs and retired generated topics are excluded from answer candidates. No similarity score, source category or search result authors a canonical edge.

`scripts/alden_osk_retrieval.py` maintains disposable FTS5 and vector projections outside the vault. The revision includes the exact note SHA, path, filtered reference body and encoding version. Edited or moved notes invalidate their vectors; deleted notes remove their document, FTS and vector rows. Query reads refresh lexical state and hash-check included notes before returning. Unresolved inventories fail closed. The supported projection budget is 4096 notes; exceeding it is explicit rather than silently indexing a subset.

The existing producer embeds changed notes using the pinned local `mlx-community/multilingual-e5-small-mlx@5030c7625865046d350eeea28f427d80353d0ac0` service, 384 dimensions and batches of eight. Complete title, summary and body are divided into overlapping 256-character windows at a 192-character stride. Each window vector is normalized; character-weighted pooling is normalized again. This bounded approximation preserves later text but is not a measured optimal representation. A service rejection keeps indexing pending. Neither the query path nor this producer starts a new model daemon.

Queries embed only the query. BM25 title/summary/body weights are 5/3/1. Dense cosine ranks and lexical ranks are merged with RRF, `k=60`, equal weights and at most 40 candidates per list. These defaults have not been tuned on an independent relevance dataset. RRF is available only when every eligible note has a vector for its current revision, model and endpoint; otherwise the reported mode is `bm25_only`.

Context contains bounded note bodies and actual resolved Body Links and predicates between included notes. Provenance preserves note ID, SHA, source coordinates, `derived-from`, `conflicts`, update time and truncation. OSK restricts authored `conflicts` to eligible open cases; an ordinary node cannot be invented as a case. Non-node case/source references remain provenance rather than manufactured node edges. Canonical authority identifies the storage source; it does not approve every saved hypothesis as truth. Room/actor filters require source evidence and exclude aggregate cross-room person notes. Time filtering in this projection refers to canonical note update time, explicitly reported as `canonical_note_updated`; original message dates belong to the raw history path.

Pure knowledge questions no longer search unrelated Kakao histories. Explicit chat questions retain the existing room resolution and quoted original-history boundary. Voice context reports note sources separately from history sources and preserves truncation flags. The voice prompt asks for short complete sentences with limitations. `finish_reason=length` is rejected before speech or conversation commit, even if the transport completed; the existing 128-token, wire-byte, model-ready and cancellation guards remain.

## Actual execution on 2026-10-05

The existing E5 service and loaded 27B model were used without restarting the five unowned workers or changing model/settings selections. Private source content and screenshots are not published here.

| Check | Observed result | Scope |
|---|---|---|
| Complete current note projection | 104 notes, 232 windows, 1.454 s | One index run, local resident E5; not cold start or p95 |
| Constructed retrieval queries | Three queries returned canonical note IDs/hashes in RRF mode | Functional checks, not independent Recall@k/nDCG |
| Knowledge answer after prompt/guard correction | Complete two-sentence answer, `finish_reason=stop`, 67 output tokens | One warm 27B request; no physical speech playback |
| Retrieval for that answer | 0.463 s, two note sources, zero history sources | One request |
| Whole request / first visible token | 4.532 s / 3.303 s | Includes retrieval and local request; no p50/p95 claim |

The preceding request had ended at 128 tokens with `finish_reason=length`, returning an unfinished list. It is retained as a failure, not successful generation evidence. Initial embedding attempts also rejected unsupported batch/input sizes; the complete run above uses the corrected bounded encoding. Different prompt lengths and cache states prevent treating these samples as a controlled speed improvement.

Regression coverage uses the real bundled SDK with temporary vaults and fake embeddings: lexical-to-RRF readiness, unchanged-index reuse, canonical byte preservation, actual predicates, room boundaries, edit invalidation, deletion, context/provenance budgets and cancellation. Voice coverage checks original-history fencing, no unrelated chat lookup and rejection of truncated model output. CI includes these voice suites. Full corpus semantic review, independent retrieval quality evaluation, physical voice/primary/tray/Retina/lock testing, Flash-Next admission, Dot invocation and signed/notarized production delivery remain open.
