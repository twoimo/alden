# Source-normalized text and graph layout affinity

The collection producer uses `retained-local-original-text-v2`. The former
semantic-affinity reader accepted only v1, rejecting valid current vectors as
`stale_text_binding`. In the installed 0.3.103 runtime, both selected real
career sources were rejected despite matching current source versions, E5
model/revision and vector encoding. A broader displayed page could therefore
lose its semantic layout inputs while retaining its structural graph.

## Exact current and legacy contracts

The reader now imports the producer's current text version. For v2 retained
graph text, it verifies the retained JSON and compares the cached body with
the exact result of `project_source_text(original, source)`. This admits the
existing narrow, reversible Threads numeric-tail projection and keeps its
original/body/tail hashes in the proof. It does not normalize arbitrary spaces,
names, dates, IDs, URLs or source claims.

Legacy v1 continues to require the exact retained original string. A v1 stamp
cannot admit a v2-cleaned body. Unknown extraction versions are rejected. The
current membership/version, stored projection, source JSON, base/text hashes,
permission, exact E5 model/revision/dimension/encoding and unit-vector checks
remain mandatory. Metadata-only and explicitly unread sources still cannot
supply semantic layout evidence. The developer CLI's strict closed-WAL refusal
remains intact; actual product checks used the existing caller-owned derived
readers and their explicitly allowed SQLite WAL/SHM read metadata.

`verified-current-projection-and-legacy-raw-v2` is bound into evaluator and
product input hashes. Each admitted proof exposes its extraction version and
optional verified text projection, with the separated tail text excluded.
The graph's source nodes, factual relation set and original versions are not
merged or rewritten. No encoder invocation, reindex or model startup is needed.

## Actual scope and validation

The two-source candidate admitted both existing vectors and returned one
layout candidate with cosine 1.0. This establishes that identical retained
texts' existing vectors were no longer rejected by a version mismatch. It does
not establish independent corroboration, shared identity or claim truth.

An actual career overview admitted 1,984 current vector nodes and compared
65,536 of 1,967,136 eligible pairs, approximately 3.33%. The existing caps returned
1,024 of 4,747 selected neighbor candidates; 3,723 were truncated. The response
remains `bounded_partial`, with the missing-pair/candidate scope explicit.
The production UI parser accepted this real payload with zero factual edges
added. This is parser/input evidence, not proof of rendered or human-validated
semantic clusters. One run observed wall 11.075 seconds, CPU 3.279 seconds and
peak RSS 102.8 MB; no speedup, steady-state RAM, GPU, battery or whole-app gain
is claimed.

Four new source-binding cases reproduce the current-version defect and check
numeric projection, forged whitespace, unknown policy and legacy behavior.
The fixed installed source failed three and passed one. A before run that
overlapped editing is retained as mixed-source evidence and is not used as
the authoritative baseline. Related semantic/layout/source-text/transport
tests passed separately. Candidate/install/render/CI delivery receipts remain
in the task's `outputs/semantic-text-version-20261014/`.

The cosine threshold and bounded neighbors remain uncalibrated against broad
human semantic/identity/time gold. Similarity is layout evidence only, not a
stored factual relation, an identity merge, or proof of equivalent physical
energy. Conflicting retained topics/entityTags and the rest of the full18
objective remain separate open requirements.
