# Real graph updates with structural and validated vector channels

This checks the installed 0.3.207 graph path and the unchanged production layout at source HEAD `200513ecb17d6b6cc49e9d92640d804ad63bbc61`. It supplies actual-data evidence for [F-02's incremental update condition](graph-cache-review-20261021.md), without promoting the separate community candidate or changing the source corpus, layout algorithm, model, embedding profile or runtime.

## Inputs and selection

The first replay uses the retained actual 1,984-node / 1,212-relation projection from the 207 title comparison. The production union, parser and `SemanticLayoutEngine` are used directly. Three existing relation pairs, one connected node and one connected pin are selected by a declared SHA-256 ordering before mutation outcomes are examined. No fabricated relation is added. The operations happen only in isolated memory; no source identity or database row is deleted.

The second replay uses a fresh installed `collection-affinity` response and a fresh installed `collection-graph` response. The older graph's two changed source-version references were detected and were not forced to match. All 1,984 IDs, source versions and target references of the fresh graph match the affinity input exactly, and the production `parseLayoutAffinity` accepts the response before any pair reaches the layout. The response is `bounded_partial`, with 320 selected vector pairs from already stored, verified E5 evidence. No new embedding inference or larger model is started.

Three admitted semantic pairs are selected deterministically from the baseline checkpoint before semantic-withdrawal outcomes are examined. This selection does not depend on whether the mutation passes. Structural relations and vector affinities remain separate channels; no affinity becomes a fact edge.

## Actual replay results

| Replay | Mutation | Unaffected nodes checked | Unaffected nodes moved |
| --- | --- | ---: | ---: |
| R only | withdraw/restore three real relation pairs | 1,257 | 0 |
| R only | withdraw/restore one real node | 1,426 | 0 |
| R only | move an existing pin | 1,426 | 0 |
| R + 320 validated S pairs | withdraw/restore three real relation pairs | 1,240 | 0 |
| R + S | withdraw/restore one real node and its incident affinities | 1,426 | 0 |
| R + S | move an existing pin | 1,426 | 0 |
| R + S | withdraw/restore three admitted semantic pairs | 1,743 | 0 |

The independent affected-region oracle traverses the union of old and new admitted channel connections. It does not reuse the engine's `active` flags. In all seven cases, surviving identities are exact, coordinates remain finite, pins stay exact and excluded from dirty output, collision/solver budgets hold, and checkpoint restoration followed by an unchanged call preserves coordinates and memberships exactly. Restoring the original topology restores the original partition, ignoring cluster-name spelling. Explicit reflow from the existing state matches a cold reflow in both partition and coordinates on these inputs. The unchanged baseline also performs no solver work. The frozen graph and affinity objects remain unchanged.

These are seven correlated perturbations of two closely related projections of one corpus, not seven independent datasets. This does not remove the older synthetic restoration, resolution or ID-renaming counterexamples, establish all incremental/cold equivalence, validate the separate aggregate candidate, or prove human semantic quality. Per-call times are diagnostic single executions; they do not establish representative latency, FPS, GPU, battery or a speedup. No native UI, physical minimization or Retina acceptance is claimed.

## Coverage and semantic limits

The installed affinity response admits 242 of the 1,984 displayed nodes. It excludes 1,742 nodes: 1,078 have empty bodies, 655 are metadata descriptions, two declare metadata and seven have noncontent source declarations. There are no validation-rejected nodes in this read. One admitted node has no same-project comparison partner; 241 nodes participate in 18,314 eligible same-project comparisons. All those comparisons are above the existing .65 threshold, and 320 pairs are returned after neighbor/degree/candidate selection. Candidate truncation leaves 254 neighbor-union candidates out. Complete comparison coverage within this eligible subset is not complete content coverage of the displayed graph.

All 242 admitted vector hashes and text hashes are distinct; no exact vector collision across different encoder inputs was observed. Selected pair cosine ranges from approximately .85648 to .99968, with median .92299. These values and the fact that every eligible comparison exceeds .65 show why the threshold cannot be treated as calibrated semantic truth. No threshold is tuned from this sample, and neither equal colors, geometric proximity, cosine nor source text is used to merge identities.

The next data gate is to distinguish unavailable bodies from deliberately declared metadata within the already authorized collection targets, then acquire actual permitted source content where possible. Independent blind human relevance/identity/temporal labels, source-diversity and no-supported-answer checks, and the preferred external cross-audit remain required before semantic-quality or promotion claims.

## Failed observation and provenance

An initial verification script put the full graph read and affinity read inside one affinity-budget owner and failed with `affinity_rss_budget` while reading display contexts. That script is not the installed controller's separate `collection-affinity` call. Its traceback is retained; no budget was raised and no failure was labeled ready. The fresh actual installed affinity dispatcher succeeds under the unchanged default budgets in approximately 3.10 seconds. This distinguishes the failing combined harness from a reproduced failure of that product call; it does not establish a general RSS or memory-causality result.

The actual product-owned read scopes permit ordinary derived SQLite WAL/SHM metadata. The successful receipt observes those sidecars appearing with zero denied write attempts; it does not certify byte-identical SHM or main database bytes. Record/index writes, checkpoints, source writes and model inference are excluded from the exercised read contract. Original files, processes, models, queues and research tabs remain intact.

Private receipts and source-bound inputs are under `outputs/graph-update-replay-20261021/`, including both replay sources, bundle hashes, preselection contracts, partitions/checkpoints, installed action responses, exact-reference comparison, vector-shape audit and the initial failure. They contain private source material and are excluded from research uploads and public repository contents. This is additional verification of the existing runtime; the full 18-item objective, ST01–13, Cursor gates, preferred thesis/adversarial/reverse review and remaining release gates stay active.
