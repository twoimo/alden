# Additive channel discovery candidate

Status: **uninstalled, isolated evaluation only**. Installed Alden remains
0.3.212. This candidate addresses the remaining ST09 fresh-channel discovery
gap; it does not complete the full18-item objective or ST01–13.

The existing native scheduler refreshes explicitly enabled video targets. It
cannot discover a channel's new IDs. Replacing the channel snapshot with a
15-entry feed would incorrectly prune older memberships. This candidate keeps
an independent additive frontier and registers separate video contexts for the
existing collection/capture/FTS path. It never creates inferred relationships.

## Source and ownership contract

`discovery_candidate.py` runs only on a private marked evaluation store and
explicitly rejects the production root. There is no installed import, new
LaunchAgent, live network adapter, automatic policy enablement or hidden host
change. `step()` receives bounded feed/native reader callbacks. The caller
must bind those callbacks to the verified host adapters before promotion.

The feed parser requires the exact registered full channel ID in the root
author URI, alternate channel link and every entry. An observed22-character
root suffix is accepted only with those independent full-ID checks. Each
video needs its exact ID, author URI and watch/Shorts link. The original Shorts
URL is retained alongside its canonical watch URL. A display title never
supplies identity. Invalid, oversized and declaration-bearing XML is rejected.
An empty identified feed and an access error cannot delete old frontier rows,
memberships or sources. Coverage is the returned feed window, not channel
history; missed uploads outside that window remain a known limitation.

The private SQLite frontier retains feed hashes, bounded attempts, native-byte
receipts, retry times and per-source states. Checks have a60-second floor.
401/429 and repeated native403 responses persist a provider hold; they cannot
be bypassed by another channel. An unresolved source exhausts its five-attempt
budget. There is deliberately no automatic hold reset or account substitution.
Feed errors retain the frontier and postpone another check.

The native envelope uses the existing video validator, including hour-format
timestamps. Original metadata, captions and native stdout bytes are retained.
Feed-derived context is labeled separately from native uploader identity.
Description-only sources retain an explicit failed-caption state. A partial
envelope cannot hide a401/429 behind that state.

Publication rechecks the parent configuration/permission fingerprint under the
existing scheduler control, abort, parent and child locks. A single insert-only
canonical transaction creates the child and its permitted project memberships.
Existing targets/documents are skipped; recovery never overwrites a changed
child definition, reenables it or grants another project. Newly created targets
keep default-disabled native polling. Their actual input files are consumed by
the existing collection/capture/index mechanisms after separate integration.

The canonical target commit and frontier receipt span separate databases.
Recovery reconciles the committed child definition and exact input bytes;
it does not claim distributed atomicity. A remotely completed read whose
response was never durably retained may need another idempotent read. Durable
receipt recovery avoids that second callback. Power-loss behavior is untested.

## Verification performed

-21 focused candidate boundary tests plus65 existing acquisition, scheduler,
  receiver and realtime tests pass on the pinned product CPython3.11 runtime.
-Two retained real public feeds contain30 entries, including12 Shorts links.
  Two already-known IDs are skipped.28 retained native responses register and
  collect as28 separate video targets in an isolated real CollectionStore.
  They preserve26 actual captions and2 explicit caption-access failures.
-All28 reach actual source capture, exact version-scoped FTS bodies and title
  queries in their permitted project. Foreign-project queries return no rows.
  Parent configurations, cursors, original memberships and versions remain
  exact. Both stores pass integrity checks; no relationships are invented.
-Two actual owned child-process evaluations exit73, respectively after durable
  acquisition and after canonical target commit. Distinct replacement PIDs
  resume the same receipt without another native callback or duplicate target;
  actual collection/capture then succeeds. This is deliberate process death,
  not OS crash or power-loss certification.
-The current feed-integrity guard is additionally checked against all28 real
  replay jobs. Malformed/tampered feed/native bytes, changed child permissions,
  pause/voice/emergency gates provider holds and a busy canonical transaction after input publication have focused regressions. A bounded publication retry preserves the original durable response after rollback.

These are cached-source replays: **zero new external requests**, production
writes, model calls or policy activations. Callback counts are not provider
request/token accounting. Source/FTS integrity does not establish semantic
truth, ranking quality, successful dense indexing, live refresh, physical UI,
whole-channel coverage or representative resource performance.

Run the maintained regression with:

```sh
python3 -B -m unittest tests.test_alden_channel_discovery_candidate -v
```

The actual source bodies, original IDs, process scopes, databases and receipts
stay in private task evidence; they are not uploaded to GitHub or research.

## Promotion gates still open

W-ST09-D1 remains a candidate. Preserve the original approved terminal204
thesis reservation and its source/bundle; this new delta must not silently
replace that request. The required ChatGPT thesis → Gemini adversarial Deep
Research → ChatGPT reverse review is incomplete. Source readiness, provider
quota and shared admission are separate from passing local tests.

Before product activation, resolve the scoped audit findings; connect the
verified public-feed/native adapters and the existing scheduler/watch loop;
add explicit per-channel CAS controls/status and hold reconciliation; verify
fairness and restart under multiple channels, live new-ID acquisition,
collection/capture/dense-index/UI readback, permission revocation, retention
and actual resource bounds. Do not introduce a second unattended host or
claim that the current callback runner is a live subscription. Installation,
physical QA, global host wiring and normal release remain separate gates.
