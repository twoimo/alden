# Exact retained-text reading folds

Legacy Threads reading folds used a stripped body while the reader requires
`reading + tail == retained body`. Leading or trailing whitespace therefore
made an otherwise eligible receipt unusable, leaving the numeric suffix in
the main reader. CR-only line endings also failed the optional fold.

Match against the retained body, preserve its exact reading/tail boundary,
and bind the body hash to those same bytes. LF, CRLF and CR remain unchanged.
The minimum substantive length still ignores outer whitespace, and the
128-character tail limit remains. This repairs the existing presentation
contract; it changes no source text, index extraction/version, stored labels,
source identity, semantic merge decision or numeric value. The numeric suffix
remains an expandable, semantically unverified presentation candidate.

Verification: the old source fails seven new subcases across the boundary
and detail/storage regressions. The repaired source passes23 source tests,
56 graph-quality tests,53 retrieval tests, and all1,159 UI tests. The UI has
three new boundary tests; its production code is unchanged. Integration
checks preserve the private source archive, collection DB and indexed rows.

The installed baseline audit covers five permitted projects,26,661 current
memberships and48,355 relations, with26,661 canonical JSON sources verified.
No structural/hash findings were reported; this does not establish semantic
correctness. Seven stored-body match groups contain36 document memberships;
they remain review candidates, not merged entities. Declared time fields
include1,045 notation conversions and459 explicit missing values; no quantity
declarations are present. Body-number interpretation and independent human
gold remain separate.

The retained-source comparison and actual detail payload fixtures are private
task evidence under `outputs/source-normalization-live-20261025/`. Raw reading
shape candidates must be distinguished from structural extraction: already
separated tails do not require a second reading fold. An initial fixture
chooser conflated these paths; the corrected comparison follows the product's
projection order. Current inspected data did not reproduce the whitespace
bug. New inputs reproduce it; broader future/semantic/physical performance
claims are not established. External current-source Pro/Gemini/reverse review
and the full goal's remaining delivery gates remain incomplete.
