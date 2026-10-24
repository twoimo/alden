# Existing messenger runtime and explicit MCP opening

Base: `0126f1198e8802218e88ae8ac3e66ff78e97d66f`. Reversible, uninstalled and
externally unvalidated. This advances F-M08-01; production enrollment, a running
collector, actual native search and the full 18/ST01–16/Cursor8 goal remain open.

## Missing path and implementation

Messenger authority previously lived under `docs/evaluation`, and the standard
MCP CLI could not construct its guards. Private probes supplied their own host.
The maintained implementation is now `scripts/alden_messenger_runtime.py` and
`alden_messenger_quality.py`. Legacy imports forward to the same underlying
objects. The publication verifier mutates the maintained implementation, and
the one manual-spec test now imports that module directly. No persisted schema,
target ID, policy, project, content identity or source record is migrated.
Compatibility imports remain until consumers/recovery tools no longer need them;
manual patching of an unregistered shim's globals is not the mutation interface.

`--messenger-source kakaotalk` opens an **already enrolled private evaluation
root** through readonly Frontier/CollectionStore connections. It never discovers
rooms, creates/adopts an account, registers targets, migrates schema or starts
collection. The evaluation marker/project remain; production enrollment is open.
Normal MCP startup and the complete search default are unchanged. Example:

```sh
"/Users/twoimo/Library/Application Support/openkakao/runtimes/menubar/bin/python3.11" -B \
  scripts/alden_knowledge_mcp.py --state-root /absolute/existing/enrollment \
  --allow-project messenger-evaluation --messenger-source kakaotalk \
  --search-strategy indexed-lexical
```

This does not activate the installed app. Request JSON cannot select a factory,
SDK path, callback, source or strategy. Telegram stays unadvertised pending its
decoder/contextual-consent contract.

## Source and request boundaries

Startup checks scope/root before native identity or collection access. The
canonical SDK's metadata-only health check must pass complete manifest coverage,
file pins and zero declared native/identity/credential/send/write actions. No
pin is rewritten and no decoder is copied. A reproduced five-module gap omitted
three function-local imports. The supported eight-module static closure is now
checked before import and loaded eagerly, including confirmation/catalog helpers;
new modules/packages or relative imports require a contract refresh. This is a
bounded static contract, not a theorem about dynamic Python imports. Canonical
module origins, capabilities and loaded fingerprints are checked. Unknown preloaded or changed loaded modules
need a fresh qualified host. Import state is serialized.

Each request repeats qualification and creates new root-bound authority with its
own cancellation closure. The initial server root is pinned. Current native
identity must match the existing binding/target; account changes fail without
adoption. Snapshots require readonly/decryption evidence and integer-zero
server/send/source-write/settings counters. Canonical failure reasons survive.
Current membership/version/projection/source/context/final checks remain.

Cancellation brackets source calls and observes the root abort state. One opaque
SDK operation or bounded health subprocess cannot be preempted by these checks.
SDK qualification is an admission interval, not atomic with later native reads
or perpetual downstream permission. Complete retrieval can maintain its existing
derived text cache; this is not source/frontier registration or migration.

## Marker counterexample

The old root check read its marker before rejecting nonregular files. An owned
private FIFO without a writer makes the actual baseline exceed a 1s child budget.
The new check opens nonblocking/no-follow, verifies regular type, owner, private
mode and exact size, then reads at most expected length+1. It rejects the same
fixture in 0.074s including interpreter startup. Only that owned timed-out child
is killed. This is a bounded startup counterexample, not a universal latency or
all-filesystem-interleaving theorem.

## Actual SDK qualification remains blocked

The real CLI stops at `kakao_sdk_integrity_unverified`. Its schema1 manifest
exists with 132 entries. Metadata comparison finds nine canonical/shared
mismatches across five distinct paths: builtin Kakao guide and chat-controls
guide, reference, Python and Swift implementation. Three chat-controls tests
are outside coverage. There are 254 successful pinned comparisons; that does not
qualify the complete bundle. Shared skills and the manifest remain untouched.

The actual standard entrypoint runs with traps before SDK import, native identity
and SQLite connection. All counts stay 0; no server starts and no body is read or
source/send written. This proves fail-closed startup, not successful native
search/collection. Resume after the shared SDK's reviewed release and canonical
health check qualify its current files. Never regenerate pins to bless unexplained
controller changes.

## Verification

- Thirteen controlled factory/loader tests cover readonly/no-registration opening,
  receipts, account changes, repeated qualification, root pinning, cancellation,
  FIFO admission, legacy object identity, module origins and loaded revisions.
- A fresh child runs the actual CLI/stdio entrypoint with a **controlled SDK**:
  fact/context success, account-change rejection, restored-account success,
  subsequent ping, clean exit 0 and unchanged source SQLite hashes. It uses no
  native data and does not qualify the real SDK.
- Six isolated source mutants detect health/receipt bypass, cached qualification,
  root rebinding, stale loaded revisions and the original five-module baseline. The maintained publication checker
  still rejects its three actual-source mutations after the move.
- 869 affected checks pass in 46 fresh pinned processes. Resource checks first
  detected missing Tauri entries, then all 7 passed after both exact allowlists
  were updated. Build-receipt 33 tests pass. No canonical SDK is bundled.

Private evidence/source bindings: `outputs/messenger-runtime-20261023/`. Real
SDK-qualified native CLI, production enrollment/collector/UI/combat, Telegram,
independent quality gold, full indexing/default strategy, physical/whole-app
measurements and current-source external review/release remain open. Installed
Alden remains 0.3.218. Earlier native reports retain their original source/time
scope and are not current SDK readiness evidence.
