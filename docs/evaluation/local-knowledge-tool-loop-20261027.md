# Typed local knowledge tool loop

Explicit local typed knowledge turns now offer one `lookup_requested_knowledge`
function with empty arguments. A real model may choose that call or answer
without it. The host resolves the original input/history/scope, executes the
existing knowledge reader once, sends a matched assistant/tool role pair, and
accepts one terminal final response. This implements the source path; actual
fixed-model and installed acceptance remain separate gates.

## Authority and protocol

The turn retains its original request/owner/conversation/turn/context/prepared
context, input/history, state root, route and InputToken marker/abort epoch.
Each boundary checks the current turn and attachment/link authority. The model
cannot provide a query, project, path, consent, alternate tool or second read.
One shared MLX request lease spans both model hops and the knowledge read.
Ordinary voice/text-only generation keeps its existing structured-call refusal.
Current storage/refusal inputs still override knowledge follow-ups.

The new path accepts only nonstream JSON, one assistant choice with strict
index0, exact observed model, bounded physical response/call IDs, and a legal
terminal reason. Duplicate/nonfinite outer JSON, legacy/unknown/multiple calls,
nonempty arguments, unexpected SSE and a second call fail without retry or
fallback. Reasoning metadata never becomes visible answer text. Only reserved
leading `<think>` wrappers are removed; an incomplete or empty visible answer
fails, while quoted/fenced examples remain literal.

There are at most two model POSTs and one knowledge executor invocation, 64 KiB
per completion, 40 KiB per tool result and 128 KiB per request. A monotonic120s
cooperative deadline wraps the original token. HTTP actively closes its socket
on cancellation/deadline, and SQLite/source readers observe cancellation.
This is **not a hard wall-time guarantee for each filesystem operation**: the
canonical OSK owner checks between files and its existing inventory work may
block within one file operation. The typed bridge's outer timeout remains135s.

## Selected source fences

An opaque, in-process receipt binds the exact selected/truncated facts and full
provenance, root inode and same token object. Domain checks reuse CollectionStore
permission/version/source/relation ownership, the canonical OSK namespace and
source bindings, and exact indexed corpus message identity/content. Unmanaged
canonical notes and title/alias relations retain the owner's identity/ambiguity
rules. There is no guessed path, first-match identity, second search or broader
scope fallback. Unsupported or changed source proofs fail closed.

The final source fence runs after model readiness immediately before the next
model POST, then after generation and immediately before the existing final
CAS. This catches revocation during readiness as well as lookup/model work.
Independent source reads and chat commit are **not cross-store atomic**. A
writer can still change a source after its latest read and before CAS; no source
authorization lease or eliminated TOCTOU is claimed.

OSK revalidation rebuilds its bounded canonical namespace snapshot on every
fence, including duplicate-ID/stem and alias checks. This has a real inventory
read cost. A selected-index optimization must retain those checks and be
measured before any performance claim. Hashes bind bytes/identity, not truth.

## Persisted evidence and verification

Only bounded physical hop IDs/models/usage, read state/mode/count, final-hop
response observations and whole-loop timing enter generation telemetry. An
intermediate tool decision never becomes the top-level final response. Failed
state combinations are checked, and the final assembled generation JSON,
including subsequent document/link evidence, must fit4KiB. Prompts, transcripts,
source bodies and opaque receipts are excluded. Nonstream TTFT is unmeasured.

Tests drive actual `process_input`, actual temporary CollectionStore lookup,
tool result role binding, final durable answer and same-request replay. A fresh
nonce is present only in the source and reaches the mocked final model only
through the actual tool result. Negative tests cover malformed/extra/second
calls, source denial/revocation/advance, stale turns, original input marker,
abort+resume, cancellation, budgets, deadlines and final metadata size. Source
authority tests also exercise real temporary corpus and canonical OSK stores.
These are model-transport fixtures, **not actual decoder tool-choice proof**.

Native Astra workers implemented the pure protocol and source fences in
disjoint files. Supplemental OpenCodex Argon/high reviewed a source bundle,
with canonical one-send routing and no tools/side-effect events. Its four
findings were adjudicated; five discriminating tests produced seven failure
entries before fixes and pass afterwards. Its premise of an existing generic
LocalMlxLlm cleaner was incorrect; the concrete leading-channel gap was still
reproduced and addressed. Unmeasured serialization/intent cost hypotheses are
recorded without savings claims. Required web-Astra/max and the original
ChatGPT→Gemini→reverse audit ownership/admission holds remain unchanged.

Current shared model readiness/owner constraints prevent a real27B/IQ3.3
acceptance run. No substitute model, shared-server adoption/reset/load,
installation, native GUI launch, original operational DB write, SDK enrollment,
permission/account/global-setting change or release follows from this change.
Alden remains OFF. The original18 requirements, all follow-ups, ST01–16 and
Cursor gates remain active.
