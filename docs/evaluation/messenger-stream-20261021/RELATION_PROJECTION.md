# Directed relationship records and displayed connections

`graph_page` groups authorized current records by the exact directed tuple
`(source, target, type)` before the display-edge limit. A160-copy focus fixture
previously returned144 duplicate API edge records and omitted another connection.
The existing renderer already coalesces geometric pairs, so those144 records
were not144 GPU lines. The grouped API view restores both logical connections.
Opposite directions and different types
stay separate. This is display normalization, not an entity merge, semantic
agreement or removal of source evidence.

Permission/project/target filters, current endpoint and relation authority, and
full relation-history binding precede grouping. Corrupt records outside the
displayed ID sample still reject the read. A denied source cannot contribute a
count, ID or revision. These properties have finite actual-store tests and
temporary source mutants; they are not a proof over every source environment.

Original `relations` and `relation_versions` remain unchanged, and `graph()`
continues to return separate source records. `total_edges` still counts those
records. The bounded view's `id`/`evidence_message_id` retain a current original
record ID for existing clients. Additive `display_id` identifies the exact
directed tuple and stays stable across representative withdrawal. The UI labels
the total as relationship records rather than implying all are separate lines.

`reference_group` supplies the current record count, a16-ID sample and an
explicit truncation flag. Count means records, not independent corroboration.
Connection weight remains1. Source body copies cannot increase this displayed
connection's force. The parser rejects inconsistent counts, samples, flags and
revision syntax. Group metadata survives view union and invalidates evidence
refresh, while layout worker inputs/cache keys exclude it.

The revision hashes every current permitted `(relation ID, version)` pair,
ordered by ID, under a domain prefix. Each UTF-8 value has an eight-byte length
prefix, so concatenation is unambiguous. A changed version beyond the first16
IDs changes the revision. This token detects projection changes under the hash
assumption; it neither authorizes a source nor proves a claim's truth. The
bounded sample is not an exhaustive source inspector. Original records remain
available to the authorized raw graph reader; a full paged evidence UI remains
a separate product requirement.

For N permitted nodes, E current records and K directed groups, existing SQL
materialization/authority still visits the permitted graph. Per-group digest
construction visits only relationships between selected visible nodes, and
internal node-reference reads skip digest/sample construction entirely. Full
history-integrity validation remains scoped over all permitted relationships.
Per-group digest
sorting costs O(k log k) time and O(k) buffered record references for its k
records; the worst group can contain E records. This is not constant memory or
an unlimited-scale guarantee. Display geometry is bounded by min(K,B), with
B=144 focus,512 ordinary or4096 overview, after grouping. The16-ID limit bounds
sample output, not the amount of evidence read. Frontend-only deduplication was
rejected because it cannot recover a distinct edge already lost at the SQL
record limit.

Task artifacts compare identical controlled SQLite inputs before and after,
alternate read order and preserve source/database hashes. Query CPU overhead,
saved displayed lines and actual GPU/FPS/battery effects are different results.
The fixture's production bridge selector changes from one geometric connection
to two by recovering the missing endpoint, not by proving a draw-call or FPS
speedup. Whole-app/native incoming activity, original installation, broad source scale,
current external audit and normal release gates remain separate.

Reproduce behavioral coverage:

```sh
python3 -B -m unittest tests.test_alden_relation_projection tests.test_alden_focus_projection
cd desktop
npm test -- src/__tests__/relation-projection.test.ts src/__tests__/semantic-layout-driver.test.ts
```
