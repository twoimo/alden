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

Installed acceptance on 2026-10-13 used Alden0.3.84 built from
9d80f7d70791f8ff892c2e42eb7d0c4732f22910. The installed four-query/six-proof
readback matched independent current memberships/raw hashes, retained first-query
facts/ranks and rejected research knowledge in jangsin-only scope. Candidate and
installed owned stdio initialization/search/negative-scope checks both passed;
this does not establish global MCP registration or two independent hosts.

The installed voice reference path actually performed a scoped E5/RRF lookup.
Its one returned proof retained requested projects, actual jangsin membership,
version/hash and policy-v4 classification identically in model-input context
and in-memory metrics (2,203 quoted characters). No LLM generation, physical
voice or durable voice-turn write was performed by that check.

Candidate and installed native audits each completed 14 states/28 PNGs in
74,429/72,209ms. Both window sizes observed affinity/layout/rest and strict
two-second render idle; all2,048 projected centers were valid/visible, with no
clipping. Eight relevant graph/quality PNGs were directly inspected. Remaining
48 PNGs, physical gestures/Retina, semantic quality and whole-app resource/FPS
improvements are not certified by these audits; central overlaps remain open.

Local focused Python168, final retrieval23 (overlapping subset), UI779,
native136, typecheck and strict Clippy passed. Source CI38267959046 passed all
four jobs. The installed68 payload hashes/modes/strict ad-hoc signature,
separate0.3.83 recovery, selected settings, seven targets/two policies, model
listeners and selected collection/retrieval/voice tables were preserved.
The local ZIP is12,848,136bytes with SHA256
4d9570af012673c82344200f736fcbf87a1afc87568863677ed7928325c06a9e;
its68 payload bytes/modes and CRC passed. PR/three-ref/documentation-CI final
readback is bound by delivery-receipt.json. Developer ID/notary, public tag and
production deployment remain separate unfinished gates.
