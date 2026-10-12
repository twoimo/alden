# Alden0.3.84: actual retrieval source scope and version proof

The installed0.3.83 mixed-project EmbeddingGemma2 query returned a research post
and a jangsin video. Each belonged to only one of the requested projects, but
both fact proofs copied the entire request into projects. The two wrong
attributions were reproduced with current read-only memberships and retained
source versions. The selected original blobs were not modified.

Each entity proof now separates requested_projects from actual permitted
projects and source_memberships. Memberships bind target_id, project, permission
and the exact selected source_version; unrequested, denied, unavailable and
other-version memberships are absent. Shared current versions may retain
multiple real memberships, with an explicit256-membership budget. Excess
coverage fails instead of silently truncating an allegedly complete proof.
Top-level projects remains the query scope.

Before publication, a selected read snapshot rechecks document/platform/original
identity, processing version, canonical projection hash/version ID and raw
SHA/path. The producer's projection contains label, body and metadata excluding
only the operational source field. JSON key order is canonical; original
identifiers/metadata are not newly Unicode-folded. Duplicate JSON keys and
nonfinite constants are rejected. Missing/revised current versions fail; valid
raw bytes alone cannot certify a changed stored projection.

Relations check both selected endpoint versions/source bytes and their stored
relation_versions record, evidence hash and actual permitted target scope.
Declared evidence is retained as declared evidence, not independently true
source content. An evidence digest binds that stored revision; arbitrary
external locators inside evidence are not followed or promoted to authority.

Policy-v4 content classification now accompanies entity proof. Description-only
facts stay searchable and are marked as original body unverified. Voice model
input and saved proof keep requested scope, actual memberships, scope_basis,
evidence version and content classification. Existing quotation fences,
truncation, cancellation and context budgets remain. The scope contract is the
selected read snapshot, not a promise that permission cannot change later.

Old regressions failed for request-scope misattribution and a changed stored
body with unchanged raw SHA. Isolated tests cover shared/version-specific
membership, denied/unrequested projects, revision/permission change after
ranking, canonical JSON order, included-field mutation, relation corruption,
large membership budgets and voice proof forwarding. Current actual-source
checks ran four project permutations and verified six entity proofs against
independent read-only SQL memberships and retained raw hashes. First-query
facts, candidates and existing E5/RRF mode remain identical.

These are source-binding/literal checks, not human semantic relevance gold,
broad Recall/nDCG/citation accuracy or model-generated factual truth. No new
embedding, model load, source write, protected original path read, merge or
schedule/permission change was performed. The full18 requirements and wider
independent gold/outlier/temporal/host/model/physical/resource/release gates
remain open. Evidence is in outputs/retrieval-source-provenance-20261012/,
whose investigation began before the date rollover.

Source tests, build, installed CLI/owned MCP/voice paths, native windows,
preservation, CI/PR/package and final receipts are separate delivery gates.
