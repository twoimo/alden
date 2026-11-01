# Version-bound source detail over scoped MCP

Graph descriptions are bounded previews. Search, graph and history previously
provided no MCP operation to read the retained detail at a selected version.
`alden_knowledge_source` now accepts `projects`, `node_id` and `expected_version`.
Use a graph node's `id`/`source_version`, or an entity search proof's
`entity_id`/`source_version`. No path, callback, limit or implicit latest-version
argument is accepted. Startup and per-request project scopes remain distinct.

```json
{"name":"alden_knowledge_source","arguments":{"projects":["career"],"node_id":"<selected id>","expected_version":"<selected source_version>"}}
```

The operation reuses `read_action` and the existing exact-detail path, with
`hops=0`, `details=true` and the expected version. It returns only `ok` and
`details`, preserving the record basis, raw archive hash, text/body format,
disclosed reading projection, capture receipt and notation binding. The body is
at most 12,000 Unicode characters with an explicit `truncated` flag. This is
version-bound source presentation, not a claim of raw-byte identity, semantic
truth, complete remote coverage or an untruncated original archive download.

A stale requested version becomes the fixed MCP tool error
`alden_source_version_changed`; it never silently switches to the new body.
Missing, out-of-scope or invalid details remain fixed unavailable errors without
exception text or private content. A missing store is not created. Existing
global-abort, client-cancellation and deadline handling cover this operation,
including nested SQLite reads.

For messenger-derived text, the cached archive is insufficient. The operation
reuses retrieval's verified source/membership and current canonical-excerpt
validator. Context authority is required, the proof is restricted to the detail's
selected target, and a detached proof/window is passed through the owner boundary.
The final metadata checkpoint must still accept that exact source/version.
Missing or changed canonical context produces `alden_source_context_unavailable`.
The bounded owner excerpt may read up to three authorized messages; this tool
returns the selected retained detail, not its neighboring messages. A different
current origin cannot authorize a detail attributed to the selected origin.

This uses application and SQLite checkpoints. It does not make archive reads,
native SDK calls, stdout delivery or cancellation one atomic OS-wide operation.
A source may change after the last check; already delivered bytes cannot be
revoked. No instant-preemption or whole-system liveness theorem is claimed.

## Verification

- 164 affected MCP, source/retrieval, cancellation, factory, context and authority
  tests pass. Existing source-guard, projection, readonly and privacy contracts
  remain covered. Controlled SDK tests are not live messenger-account proof.
- Six isolated executions of modified runtime source are detected: missing
  version, startup-scope substitution, missing SQL cancellation, missing current
  context, callback rebinding and substitution of another current origin.
  The actual source files and installed files were not mutated by these probes.
- Actual owned stdio processes distinguish installed0.3.219's three knowledge
  tools from the source's four. Three existing career records, including the
  largest current stored body selected in that project, return version-bound
  detail with independently matched archive bytes. Their bodies are 24,54 and
  1,607 characters. Long-body/12,000-character clipping is a synthetic boundary
  control; these live records do not prove it or independent semantic gold.
- Old-version, outside-startup scope, extra-argument and write-tool requests are
  rejected; subsequent ping succeeds and each owned process exits0. Only career
  bodies were read. Startup retains the previously approved five-project scope.

The additional factory control initially exposed stale cached text after a
canonical message edit. Current-context verification fixes that path. A separate
two-account fixture was rejected by the existing same-platform account pin;
the multi-origin control uses the supported KakaoTalk/Telegram fixture instead.
These failed attempts are retained in private task evidence.

## Host and delivery boundaries

The existing global Codex connection remains metadata-only. Aside's existing
local account was observed through ephemeral, incognito, model-free settings
reads: its OSK command/arguments match the maintained vault entrypoint and its
stored inventory lists12 tools. That cached inventory is not an actual Aside
MCP read/authentication attestation. One documented `messageRows` call was not
callable despite CLI exit0; the failure is retained and no substitute transcript
reader was used. No browser actions or model requests occurred.

The human authorization remains “이번 Alden 작업에서만 사용”; neither global
configuration nor saved tool registration was expanded. Installed Alden remains
off. Candidate files, actual packaged stdio, remote CI, installation, independent
hosts, external review and release each require their own evidence. The original
full18/ST01–16/follow-up/Cursor gates remain open where not independently proved.
