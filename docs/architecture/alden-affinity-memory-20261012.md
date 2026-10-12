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

## Local delivery observation

0.3.78 was built from `a844b6ca4751cdf371095a936f967339bf30b271`;
source CI 38216321446 passed all four jobs. The canonical build and installed
68-file hash/mode manifests match, strict/deep ad-hoc signature verification
passed, and an independent 0.3.77 recovery copy is retained. The selected
settings, seven targets, two acquisition policies, model listeners and
collection/retrieval/voice tables matched before and after the actual checks.
This remains selected-data preservation, not a universal chat/queue/file audit.

Candidate and installed native audits each completed 14 workspace states and
28 captures. Both sizes had applied current graph affinity, settled layout and
a stopped idle renderer. The installed audit took 60,644ms; it observes an
owned read-only WKWebView, not the primary process or physical input. Eight
affected images were directly inspected. The prior compact RSS rejection was
not erased or counted as a success. Unchanged synthetic entity workflow
evidence remains the dated 0.3.77 check, rather than a repeated 0.3.78 claim.

A separate installed full menu CLI request matched every deterministic field
and candidate order in the controlled baseline. Its inner peak RSS was
125,108,224 bytes and outer peak 130,056,192 bytes, below the unchanged guard
in this run. This is a fresh installed observation, not a universal guarantee
or a before/after production performance estimate.

The package is for local ad-hoc installation. Developer ID, notarization,
release tags and production deployment remain separate unresolved gates.
