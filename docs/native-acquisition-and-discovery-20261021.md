# Native acquisition and fresh channel sources — 2026-10-21

Existing video reacquisition and a bounded fresh-source batch now run through
the installed collection/capture/index paths. They remain separate from a
continuous channel-discovery producer, semantic evaluation and normal release.

## Existing video automation

Eight previously registered video targets had native reacquisition disabled.
Their existing CAS controls were enabled with private previous-value backups.
The original collector completed all eight first checks automatically, without
manual requests:24 native metadata/track/transcript method calls, zero added
document IDs, eight raw-source revisions and eight unchanged-text vector reuses.
Original captions and input backups match exact bytes. Together with the two
previously enabled policies, ten existing videos now have native reacquisition.

The configured six-hour base intervals remain. Realtime mode has its separately
declared minimum60-second remote check interval; it does not mean those native
checks wait six hours. Existing global pause, voice, emergency, permission and
backoff gates remain. No model, account, browser tab or additional paid capacity
was introduced. Ongoing background reads are outside the24-method first-check
count; no token, battery or CPU saving is claimed.

## Bounded channel discovery and acquisition

The two declared public channel feeds returned15 entries each. Exact channel
author URIs and every entry's full channel ID were verified. These observed
feeds use the22-character suffix at the root, so that field alone was rejected
initially; the full URI/entry evidence was required before normalization. The
documented push-topic URL returned static XML and was not treated as an empty
channel. WebSub notifications require a callback server; this run creates no
subscription or server. [YouTube push-notification contract](https://developers.google.com/youtube/data/guides/push_notifications).

Of30 returned IDs,28 were absent from the canonical store. Selection and input
hashes were fixed before native results. All28 native metadata/track reads
completed;26 supplied actual timestamped captions. The other two preserve
caption-access failure and retain their available descriptions. There are
705,345 caption characters and724,539 description/caption characters in total,
with84 invoked native methods. Source bodies, IDs, URLs and private feed/raw
artifacts are not published here. Feed context remains distinct from the native
metadata API's unavailable stable uploader ID; no identity merge is inferred.

The new source inputs reached28 separate video contexts in the same canonical
store and two existing project scopes. Existing parent-channel snapshots were
not narrowed. The existing realtime file watcher consumed every new context
without a manual request. All28 first attempts completed capture and ready
indexing:28 added canonical IDs,28 changed embeddings and3,791 indexed windows.
Those are reported indexer workloads, not backend request/token counts.

The canonical document count is26,651 and target count43. The28 new targets
consume their acquired snapshots and retain default-disabled recurring native
polling. They are not38 newly enabled remote pollers. Continuously discovering
future channel entries and reacquiring their content remains an implementation
and review gate; the one bounded discovery batch does not complete it.

## Actual artifact and preservation checks

The batch exposed native hour-format timestamps that installed211 rejected.
Runtime0.3.212, code revision
`e8be0ba39ebdbfff87b74fb9d897f7e2bbeeb36f`, accepts the retained1,890-cue response
without rewriting source clocks. Its affected source suite passes91 cases;
signed staged/installed adapters each pass12 cases plus the actual retained
response. See [hour-caption correction](native-hour-captions-20261021.md).

The installed/candidate78 file hashes/modes match; fixed local strict/deep
signatures and the independently preserved211 bundle verify. Only
`alden_collect.py` changes among resource scripts. The changed imported parser
was adopted with a cooperative reload of the same owned collection service:
original PID11941 was confirmed stopped before the same job resumed as19203.
Queue/control state, original model/enrollment/emergency settings and the
LaunchAgent definition are preserved. New long-caption collection succeeds in
the resumed process, independently of the artifact probe.

Each of28 sources matches its exact native input, original capture, immutable
raw record, current permitted version, complete indexed text and pinned
384-dimensional unit E5 vector. The actual installed source-detail dispatcher
and an owned read-only MCP child return all28 matching versions. Eleven long
popup bodies retain the explicit12,000-character prefix/truncation contract.
Foreign-project and unsupported mutating tool calls are rejected. This does
not establish global host registration or physical/native UI interaction.

Separate collection/scheduler/realtime recovery snapshots were retained before
policy activation and again before discovery publication. All six backup file
hashes remain exact. Before-activation64,842 version rows and118,555 relation
versions match the later recovery; before-discovery64,874 version rows and the
same relation history match live immutable rows. Both original channel
membership sets, original target configurations/permissions and the complete
manual-request ledger are retained. These are separate SQLite snapshots, not
an atomic cross-database or new whole-app/OS recovery certification.

The preservation harness initially assumed a single `id` key for relation
versions; the error is retained and the corrected comparison uses the actual
composite primary key. No integrity assertion was weakened. The initial parser
test command's nonexistent module is also retained separately from real parser
failures. Private receipts are under
`outputs/source-acquisition-batch211-20261021/` and
`outputs/hour-caption212-20261021/`.

The full18-item objective, ST01–13 and Cursor gates stay open. Independent
semantic/identity/temporal gold, preferred external three-stage review,
continuous fresh-channel discovery, actual model answers, broader hosts,
physical voice/UI, representative resources, DB writer lifecycle/retention and
Developer ID/notary/production release still require their own evidence.
