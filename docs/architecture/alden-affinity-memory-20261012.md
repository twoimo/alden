# Release the graph page before affinity admission

The collection-affinity reader first obtains the authoritative graph page and
extracts ordered ID/version/target references. It used to retain the full page,
including descriptions, contexts and edges, throughout vector admission and
scoring just to read its revision, total-node count and next offset afterward.
The reader now captures those scalars after the existing expected-version
check, then releases the page before evaluation. Source selection, ordering,
permissions, retained-source/text/version/profile checks, scores and budgets
are unchanged. No model inference or embedding generation is introduced.

A weak-reference regression fails on the preceding source while the full page
overlaps vector admission. After the change, the page is released before that
stage and the complete deterministic reply still matches. Pinned Python tests
pass 87 cases, the affected UI consumers pass 26, and native tests pass 136.
The first test invocation used a nonexistent packaging module; that harness
error is retained separately from the corrected pinned run.

Five alternating fresh-process full menu CLI pairs used the same real retained
E5 inputs and the existing budgets. Both adapter versions were imported in
each process before selecting one. Every deterministic response field, byte
counter, ordered candidate, proof, exclusion count and binding matched in all
five pairs. Runtime timings/RSS and SQLite observation metadata were excluded
from equality, rather than treated as deterministic content.

| Full CLI observation | Previous page retention | Release before admission |
|---|---:|---:|
| Median process peak RSS | 127,746,048 bytes | 122,470,400 bytes |
| Median inner peak RSS | 126,599,168 bytes | 117,866,496 bytes |
| Median elapsed time | 3.4269 seconds | 3.1011 seconds |

The outer peak includes final stdout serialization. The 5,275,648-byte median
reduction is 4.13% in this measurement; it does not prove UI/GPU/battery gains
or universal memory-cap compliance. The original 134,217,728-byte RSS guard,
eight-second CPU budget, response limits and source validation remain. The
0.3.77 installed compact RSS rejection is preserved as a separate observation.
Candidate and installed current-graph application at both sizes remain a
delivery gate for 0.3.78, distinct from this controlled CLI measurement.

Central concentration, whole-graph screen overlap, human semantic gold and
the remaining eighteen-requirement goal gates are not certified by this change.
Task evidence is under `outputs/affinity-memory-20261012/`.
