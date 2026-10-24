# Multi-project vector metadata scope — 0.3.196

An unchanged multi-project indexing run could rewrite existing vector rows. Each project's source-text refresh replaced the temporary metadata scope, so the final vector probe covered only the last project. Earlier projects' existing vectors were mistaken for missing entries and copied again with `INSERT OR REPLACE`. This is a cache/write defect, not evidence that stored vectors or project permissions were incorrect.

`_index_rows` now restores the union of the already admitted document/version keys after the per-project text refreshes. A version still current in a different authorized project stays in the union even if another project has a newer version of the same document. Single-project indexing retains its existing path. Cancellation checks surround the union update. No persistent schema, source identity, encoder profile or permission policy changes.

## Evidence

- A real isolated SQLite `AFTER INSERT` trigger reproduces the unnecessary writes before the repair. Reindexing unchanged inputs now writes zero vector rows for either project order, duplicate scope entries, a final empty project and a single project. It preserves exact vector rows and source-store bytes, and calls no encoder.
- A genuine raw-metadata revision with an unchanged encoder input creates a retained source version and copies exactly one vector. A changed body embeds and inserts exactly one new vector. Project-specific retrieval still returns its admitted body and provenance; prior vector history remains present.
- The final affected suite passes **123 tests** across collection retrieval, snapshots, context duplicates, acquisition, scheduling and acquisition inputs. These use isolated stores/fake inference, not live sends or model calls.
- A read-only audit of the actual five-project store found **26,623** current union versions and **26,163** current vector stamps. The previous last-project probe omitted 25,045 stamps in forward order and 26,162 in reverse order. The repaired union matches an independent full-metadata oracle in both orders. Main stores were opened read-only; only temporary scope rows were written. This is scope coverage evidence, not whole-app latency or a live full-store indexing benchmark.
- Twelve retained-source comparisons found real view-count and/or thumbnail changes behind same-body versions. Those observations remain versioned. Removing raw metadata or collapsing source history would hide genuine changes and is not part of this repair.

Reproduction, logs and the sanitized actual-data receipt are in `outputs/source-observation-churn-20261020/` of the delivery checkout. The exact delivery revision, installed resource verification and CI result are recorded there after delivery.

## Remaining DB decision

The queued external DB thesis must include this delta: the earlier single-project metadata-query result did not establish complete multi-project scope coverage. SQLite-versus-alternative decisions still require the complete source-bound cross-audit, matched correctness/recall/resource/recovery trials and separate release gates. This narrow reversible repair does not select a different engine or complete the full Alden goal.
