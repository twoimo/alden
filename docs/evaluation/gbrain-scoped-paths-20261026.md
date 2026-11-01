# Scoped GBrain paths and Open Ontologies reference

Alden can optionally query a separately provisioned local GBrain graph through
`alden_knowledge_paths`. The response returns current Alden IDs, source versions,
explicit relation groups and registered source origins. Equal titles or publisher
labels do not merge identities. Existing graph/search/source tools retain their
default behavior; the new tool appears only with all four explicit startup paths.

## Source and trust boundary

Reviewed GBrain source/tag: `garrytan/gbrain`, `v0.60.451.0`,
`33d73cb54c14e2e988272764ddff59a5ead10915`, MIT. The official darwin-arm64
asset is 187,472,354 bytes, SHA-256
`7e5ea749d4fbd7c45abd96b40fc893a0b9dcba3ee8202f7f68e68ae8949277a2`.
The client checks those bytes and runs a private stdio child with read-only tools,
an explicit `GBRAIN_HOME`, local PGlite, disabled embeddings, no inherited provider
credentials and disabled serve sweeps/sync IPC. No global GBrain/host installation
is performed. The CLI later offered 0.60.452.0; that offer was not adopted or
validated. This report does not describe the reviewed pin as the latest release.

`alden_gbrain_projection.py` exports graph labels and IDs, not retained body or
description fields. Labels can still contain source-derived text. Page and origin
slugs include the entire projection generation. Source publication times are not
replaced by GBrain's insertion-time `effective_date` fallback. Original relation
names map to explicit, opaque schema verbs and map back to Alden relation proofs;
no temporal validity, entity equivalence or semantic inference is asserted.

`alden_gbrain_bridge.py` loads one metadata manifest for the fixed
`collection-graph` query `{projects: <explicit scope>, limit: 50}`. The original
read boundary and current source authority run before the GBrain read and again
before return. An original version, authorization, scope, relation or source
membership change invalidates the result. Every returned edge must match its
generation, endpoint IDs, direction and evidence context. A bounded BFS over the
authoritative projection checks the exact edge set and depths. Missing edges,
including a false empty result, are errors rather than complete graphs.
Completeness means this single bounded snapshot, not the entire corpus.

`alden_knowledge_mcp.py` accepts `--gbrain-root`, `--gbrain-receipt`,
`--gbrain-home` and `--gbrain-manifest` together. Tool callers supply only allowed
projects, a focus ID, depth 1–3 and direction. They cannot supply runtime paths,
perform imports or widen the startup scope. A stale/incomplete cache produces
`alden_gbrain_refresh_required`; other bridge failures are sanitized. Cancellation
and deadlines propagate to the owned stdio child. Upstream setup notices remain
inert metadata; their suggested commands are never executed.

## Open Ontologies reference

The user's second reference is pinned at
[`fabio-rovai/open-ontologies@ef6e781`](https://github.com/fabio-rovai/open-ontologies/tree/ef6e781c71aaccaa35c2a1cd06169f0c862d5769), MIT.
Relevant source was read locally. No upstream skill, setup script, Rust engine,
certificate checker or global MCP registration was executed/adopted.

| Read source | Applied to this candidate | Limit |
|---|---|---|
| [Trusted computing base](https://github.com/fabio-rovai/open-ontologies/blob/ef6e781c71aaccaa35c2a1cd06169f0c862d5769/docs/trusted-computing-base.md), TCB-6/7/8a | Explicit current scope/version binding; assertion and derived-cache separation; detect dropped/added result edges | This is a runtime comparison, not a Lean theorem or a proof of source truth |
| [Planner](https://github.com/fabio-rovai/open-ontologies/blob/ef6e781c71aaccaa35c2a1cd06169f0c862d5769/src/plan.rs) and [owner tests](https://github.com/fabio-rovai/open-ontologies/blob/ef6e781c71aaccaa35c2a1cd06169f0c862d5769/tests/plan_ownership_test.rs) | Task-owned manifest/runtime, exact generation, source-bound write/read receipts | No ontology migration/apply/rollback subsystem was implemented here |
| [Modules and conservativity](https://github.com/fabio-rovai/open-ontologies/blob/ef6e781c71aaccaa35c2a1cd06169f0c862d5769/docs/modules-and-conservativity.md) | Keep structural path acceptance separate from semantic claims | OWL/RDFS conservativity, SHACL and certificate verification remain unrun |

## Verification

The task-owned public reference store contains four captured primary-source
excerpts from two publisher origins and one hyperlink verified in captured HTML.
The current Alden read API produced four nodes and one explicit relation group.
The projection persisted six metadata pages and five edges (four source
memberships plus one hyperlink), with independent page/version readback.
The actual Alden Python MCP → pinned GBrain subprocess chain passed eight
direction/depth queries, including a legitimate empty incoming path. Both
publisher walks returned three nodes, respectively three and two edges, without
cross-origin merging. Invalid project scope and Boolean depth were rejected.
The source collection database hash was unchanged throughout these reads.

267 affected tests passed. After a test-only assertion-context correction,
all 29 GBrain tests passed again. Five isolated mutations were each detected:
accept partial paths, ignore current versions, reuse an old origin generation,
ignore edge context, and accept an unreviewed notice. These are authored regression
checks, not independent human semantic gold or measured performance gains.

Local receipts reside in the task's
`outputs/gbrain-upgrade-20261025T154408Z/`: `open-ontologies-source-pin.json`,
`runtime-receipt.json`, `schema-acceptance.json`,
`alden-projection-typed-integration.json`, `alden-gbrain-mcp-acceptance.json`,
`affected-tests-final.log`, `gbrain-tests-final.log`, and
`gbrain-negative-controls.json`. Earlier rejected schema shapes and the fixture
root-mode initialization failure are retained with their corrections.

## Remaining gates

The installed Alden 0.3.219 remains off at the user's request. This candidate does
not establish installed/native UI acceptance, general private-corpus acceptance,
managed incremental imports, physical deletion of withdrawn cached generations,
or a completed release. GBrain is opt-in and its external runtime is not silently
downloaded or embedded in the application. Existing task/project permission and
global knowledge-MCP registration holds remain. ChatGPT/Gemini/reverse audit
stages are NOT_RUN under the inherited readiness/coordinator holds. The original
full Alden goal remains active.
