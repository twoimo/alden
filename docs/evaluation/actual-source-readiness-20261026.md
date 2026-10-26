# Actual source readiness and read-only replay

This phase moves from public/fixture evidence to the existing ordinary-source
collection, while preserving the user's app-off instruction. Product modules
remain at code `11138703b1e408082fe2245f3115338e1726b018`, identical to the
signed 95-file 0.3.220 candidate's product code at `5f38c55`. This report changes
evaluation records only; it does not update the installed 0.3.219 app.

## Existing collection and actual changes

The five declared scopes are `alden-model-research`, `career`, `jangsin`,
`sparkuniverse` and `youtube-tzuyang`. Their 43 targets produce a complete
query-only source snapshot with 26,651 nodes and 48,355 relation groups/claims.
This is existing stored data. No new collection, messenger registration or wider
account coverage is inferred from the counts.

The original collector/model processes keep running. The first source export's
completion checkpoint had advanced; it remains an honest consistent as-of
snapshot, not a claim of current state. A later complete comparison confirmed
equal target/edge rows but changed node rows. The stale-source guard remains
correct; no equality/permission check was relaxed to serve that old snapshot.

An actual journal-assisted source update then crosses 462 events, selects ten
candidate nodes and fourteen candidate relation groups, and conditionally puts
five node rows. No targets/edges are changed or deleted. The resulting archive
passes full canonical source comparison and has an unchanged completion
checkpoint. Its elapsed source-update time is 8.25s; without a matched full
baseline this is not a speedup claim for the actual collection.

The stable-format graph index covers 26,694 pages (26,651 records and 43 origins)
and 75,006 links (48,355 observed groups and 26,651 selected-version memberships).
Index reuse conditionally updates five pages and five links. Its complete
fingerprint and ordered logical page/link data match a separate full index
rebuild from the same new source archive. These are immutable source/index
artifacts. No 26k-record GBrain engine was built, queried or accepted, and no
active engine pointer or fabricated opaque revision/ready receipt was created.

## Actual MCP versus registered host capability

An owned stdio `alden_knowledge_mcp.py` process reads the actual source root with
the explicit five-project startup allowlist. It advertises four read-only tools.
Five graph requests each return one node whose source contexts stay in the
requested project. Two bounded history requests succeed. An outside-allowlist
request is rejected with a JSON-RPC error and no content; the server remains
usable and exits cleanly. There are seven accepted content-metadata requests and
one rejected request. No body search or source-detail request occurs. Private
labels, identifiers and history replies remain local in mode0600 files inside a
mode0700 directory and are excluded from this report.

The already registered current-host `alden_readonly.alden_status` tool also
executes successfully. It reports installed 0.3.219, reachable embedding endpoint,
unavailable generation endpoint and no voice/UI validation. The current host's
actual configured/exposed Alden connection contains only that status tool and
points to the installed status script. The owned knowledge stdio run proves this
executor's data path; it does not establish registered knowledge tools in Codex,
Aside, Alden UI or OSK. No connector setting was changed. OSK aggregate freshness
from the status response is a dated metadata observation, not source/scope
attestation or a synchronization receipt.

Standalone MCP maintenance observes a stable metadata/configuration snapshot.
Its code2 result retains two unrelated OpenCode freshness gaps; those are not
reclassified as failures of the successful Alden status read, nor repaired by
changing unrelated pins, models or connections.

## Actual native SDK hold

The real canonical Kakao SDK loader stops with
`kakao_sdk_integrity_unverified` before current identity, credentials or DB
content are read. Passive diagnostics identify a 136-entry shared manifest with
two changed existing control-skill files and one uncovered control test:

- `kakaotalk-chat-controls/SKILL.md`
- `kakaotalk-chat-controls/scripts/chat_controls.py`
- `kakaotalk-chat-controls/scripts/test_unstarted_file_resume.py` (uncovered)

This is not an OS permission-denial observation and does not establish current
account, catalog coverage or native collection readiness. The original
collection has no production messenger enrollment. The reader safety contract
requires: “Regenerate the manifest only from an authorized reviewed release”.
Existing edits, tests and trust pins are preserved; no manifest regeneration,
alternate reader, account/permission change or real test send is performed.
Native collection remains open until that shared release is reviewed/restored
through its actual owner/source contract and the canonical loader succeeds.

## Corrections, preservation and remaining gates

The first diagnostic row comparison used newline framing instead of the
exporter's eight-byte big-endian length framing. It is explicitly excluded;
the corrected protocol is independently calibrated against the archive receipt
before comparing current rows. The first MCP harness expected tool `isError`
instead of the valid JSON-RPC rejection; the corrected complete session is the
accepted result. For standalone index replay the completed immutable seed's
actual file token is captured and rechecked. It is not an engine acceptance.
Rejected/incomplete task-owned artifacts remain distinguishable and are never
used as current publication proof.

Thirteen prior app manifests, five config/profile entries and both protected
process argument hashes remain exact. No original operational DB writes,
enrollment, SDK repair, service restart, global connection, model/account change,
UI launch, send, installation, merge/tag or release occurs. Desktop and owned
engine counts remain zero. Actual source snapshots/indexes and private MCP
replies stay local; no user corpus is attached to the PR or external models.

Next work must preserve these strict current-source guards while addressing
source advancement during long unpublished engine builds/catch-up. A successful
as-of index does not prove an always-current live engine service. Real native
SDK/account acceptance, service/host/OSK binding, representative private/native
gold, installed3D/GPU/UI/lifecycle/resource validation, independent/formal and
external review, required unavailable web-Astra/max worker and normal release
remain open. The full original18/follow-up/ST01–16/Cursor goal stays active.

Private task evidence is under `outputs/native-binding-20261026T001540Z/`:
SDK/manifest/readiness observations, actual full/delta archives and indexes,
complete comparison receipts, final stdio acceptance, MCP maintenance and
protected-state readback. Only sanitized counts/methods/hashes appear in the
adjacent JSON.
