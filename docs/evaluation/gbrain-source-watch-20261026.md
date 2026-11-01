# Source-change consumer for full GBrain caches

`alden_gbrain_watch.py` watches one existing, explicitly bound full cache. It
detects committed source changes and declared authority changes, invokes the
existing complete importer, and advances its accepted source checkpoint only
after independent cache/source readback. It supplies `once`, `run` and `status`;
it does not install a service or change collection/MCP registrations.

The GBrain publisher pin and open-ontologies design references remain those
recorded in `gbrain-scoped-paths-20261026.md`. In particular, the original source
authority, derived graph and acceptance receipt retain separate meanings. This
consumer is runtime reconciliation, not ontology certification or a proof that
source assertions are true.

## Observation and recovery

A persistent SQLite connection opens the exact original collection database
with `mode=ro` and `query_only=ON`. It holds no idle read transaction. Each probe
also opens a fresh authorized collection view and reads its scoped journal,
authority digest and source-file identity. Connection-local `data_version`
catches unjournaled database commits; a file token catches replacement and
main-file changes. Native revision/authority callbacks remain those of the
existing source provider. No out-of-project source callback is requested.

Equal probes may skip a full refresh only within the same live consumer and
accepted cache. Startup always performs full reconciliation; a saved
`data_version` never authorizes a skip after restart. A periodic full audit
(default 60 seconds) covers changes not represented by a source journal token.
The default poll interval is five seconds. Global database commits can trigger
conservative rechecks even when they do not change the selected projects.

Every consumer owns a private, nonblocking advisory lock; replaced owner/lock
files stop it before another source read. Receipts validate exact root/project
binding, field types, states and opaque checkpoint/generation formats. `status`
reads the stored receipt only. Its PID and timestamp do not assert current
liveness, current source access or a running scheduler.

Before and after refresh, the consumer compares the source probe and the
independently read accepted cache/archive receipt. A changing source leaves the
checkpoint pending. Transient source IO preserves the prior acceptance and
retries. An uncertain consumer-receipt save is not overwritten or replayed:
later reconciliation inspects the already published cache. The actual process
crash control exits after cache publication but before any consumer receipt;
restart confirms that generation without repeating engine imports.

## Withdrawal

A known authority change or journal reset retires the old generated files
before exporting/building the remaining source. Full declared scope denial
removes every owned generation and active pointer. It remains retryable so an
explicit later policy restoration can be observed. Missing/unavailable source
authority also withdraws owned data, then stops the consumer. An OS permission
denial stops without another reader or a permission-repair attempt. An unreaped
engine worker retains its protected generation for existing lease recovery.

An explicit external cache withdrawal stops the live consumer instead of
automatically recreating the cache. A new explicit consumer start may rebuild
it. Filesystem changes and original source policy are not one atomic
transaction; fresh read-side authority checks remain required. Removal here
means absence from owned generated directories, not erasure of backups or
storage blocks.

## Use

Create the full cache with `alden_gbrain_import.py create` first, using the
reviewed runtime receipt. Then run, with the exact same source/project binding:

```sh
python3 scripts/alden_gbrain_watch.py run --root /abs/existing/full-cache \
  --state-root /abs/existing/collection --project exact-project
python3 scripts/alden_gbrain_watch.py once --root /abs/existing/full-cache \
  --state-root /abs/existing/collection --project exact-project
python3 scripts/alden_gbrain_watch.py status --root /abs/existing/full-cache \
  --state-root /abs/existing/collection --project exact-project
```

Native Kakao originals additionally require the existing explicit
`--messenger-source kakaotalk` factory. `--max-ticks` is available only for a
bounded `run`. SIGINT/SIGTERM and the existing source abort token stop the owned
consumer without clearing another owner's latch. CLI errors contain fixed
public codes; source exception strings are not logged as status.

## Observed evidence and limits

451 affected Python tests pass, including 26 watcher tests. They cover unchanged
probes, unjournaled commits, periodic audits, file replacement, journal reset,
source races, native revision controls, partial/full denial, explicit policy
restoration, source unavailability, transient IO, permission-denial controls,
uncertain receipts, abort, unreaped workers and owner replacement. Eleven
isolated source mutants fail the relevant assertions; the original product
source is unchanged by those controls. These are local tests, not an
independent external review.

The actual pinned engine replay uses a copy of the previously frozen public
reference fixture: four source nodes, two registered origins, six pages and five
links. A separate writer process changes a source version and declared target
policy. The running consumer publishes that version; restart reconciles with
no engine import; manual withdrawal stops it; partial denial removes the old
generation before rebuilding survivors; full denial leaves zero generations
and no active pointer. Five MCP queries include three accepted current paths
and two rejected withdrawn paths, all with clean exits. A deliberate process
crash exits 23 between cache acceptance and the consumer receipt; the following
start reconciles the existing generation. The frozen original fixture remains
byte-exact. This is a controlled public copy, not a publisher update or real
native-account permission revocation.

Evidence lives under `outputs/gbrain-source-watch-20261025T205836Z/`; the JSON
companion binds the exact source hashes, source replay and negative controls.
No corpus text or private cache is committed. Candidate package/CI evidence is
recorded separately after the commit, rather than attributed to this source
report before it runs.

The consumer still invokes **full source export, verification and graph
rebuild** when required. Engine row/link deltas, native event/service
installation, original collector integration, representative private/native
gold and independent-host acceptance remain open. There is no measured
latency/token/memory saving, formal revocation/liveness proof, installed-GUI
acceptance or normal release claim. The user's app-off instruction remains in
force. Required web-Astra/max worker and ChatGPT/Gemini/reverse validation remain
NOT_RUN under the existing availability/source-upload/readiness/coordinator
holds; no model, account, permission or browser fallback is used. The complete
original 18/follow-up/ST01–16/Cursor objective remains active.
