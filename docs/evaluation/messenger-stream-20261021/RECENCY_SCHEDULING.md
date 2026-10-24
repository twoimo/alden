# Recent activity hints with full-room fair sweeps

The earlier production collector spent four steps on history, four on live
pages and one on reconciliation. Each mode selected the least-checked room,
breaking ties by room ID. After enrolling a large cache, a recently active room
could wait behind many inactive rooms before its first page was even primed.

The qualified native Kakao catalog already orders rooms by `lastUpdatedAt`
descending. Its adapter now declares that order as a scheduling hint. No source
SDK change, new body query, model or watcher is required. The first 16 rooms form
a preferred cohort, while the complete catalog is still seeded and enrolled.
Readers without this declared order retain the earlier fair schedule.

The same nine-step budget now interleaves history and live reads. Two of each
mode's four slots may prefer the recent cohort; the other two remain full-room
fair selections. Reconciliation remains a full-room fair selection. A preferred
history slot favors only unprimed rooms, so an active room's old history cannot
continually displace another room's first page. Within the preferred cohort,
durable mode positions come before catalog rank. Pending pages and activity
recovery still precede new selections and charge their actual persisted mode.

Preferences are bounded to 64 distinct IDs at the internal boundary (the current
producer supplies at most 16), bound as SQL values, and cannot admit an unknown
room, bypass eligibility, backoff, current-source authority or publication gates.
They do not change source identity, truth, normalization, stored page content,
graph relationships, camera state or activity semantics. A metadata order change
does not create a knowledge event or combat effect.

Native messenger targets are polled by that same scheduler, independently of
file-change hints. The file scanner must not label them unsupported merely
because they have no input path. It removes only obsolete file hints for those
targets; their source cursors, durable manual requests and native availability
errors remain owned by the collector. Unknown file adapters still report their
existing unsupported-input error. No raw messenger DB file watcher is added.

## Scope and cost

Let R be eligible rooms, P retained pages, Q pages still marked prepared and H
the preferred cohort size. Each selection still examines room candidates and
uses a temporary ordering structure. Preference evaluation adds bounded H work
per candidate; it does not make selection constant-time. Catalog observation
and seeding also still cover the full room set.

The actual selection query previously used a correlated full scan of `pages`
for each candidate room: its observed plan was `SCAN p`, allowing O(R*P) visits
when all retained pages were complete. A partial `prepared_room_pages(room)`
index restricted to `state='prepared'` now excludes completed history from that
lookup. The observed plan uses `SEARCH p USING INDEX prepared_room_pages`.
Matching prepared/retired rows, room scans and ordering still cost work. The
index has O(Q) entries and adds maintenance work at prepared-state transitions;
it is not a proof that the whole collector or retrieval pipeline scales linearly.
Fresh content revision checks still have their separately documented candidate
and text budgets.

Fair progress assumes a finite eligible set, recurring fair slots, eventual
completion of pending recovery, and permission/source availability. Continuous
new enrollment, backoff, source failure and an unbounded recovery stream can
change the wait. Quiet rooms may receive fewer immediate live slots than with
the earlier pure fair policy. This candidate does not guarantee five-second
delivery for every room or establish globally optimal scheduling.

## Verification

The integration counterexample first failed because an older room ID was read
before the declared recent room. Regressions now cover recent priming without
draining its old history, recent/fair progress together, catalog-rank ties,
unknown hints, invalid input, pending recovery and its actual mode, fallback
ordering, source revocation, and index migration without data changes. Process
variants that ignore preferences or remove the fair lane are rejected, then the
restored implementation passes.

A matched owned 24-room workload ran the actual earlier collector from
`16e225b57467a0df83e93a4142e134a6ffe2814b` and the current collector, both using
the current Frontier's unchanged default-compatible path for the baseline.
The newest room's first source read moved from position 46 to 1, and its first
live step from 50 to 2. Both made 64 source page reads across eight cycles and
ended with identical documents and room checkpoints. Their initial activity
baseline responses were empty; this did not establish absence of readiness
records or validate live effects (see [the correction](STREAM_IDENTITY.md)). These
are scheduling-order measurements, not real elapsed latency or quota savings.

A separate SQL-only experiment captured the actual selection query from
`Frontier.step`, then ran identical in-memory snapshots with and without only
the new index: 200 rooms and 8,001 completed pages, SQLite 3.46.0, two warmups and
seven samples with alternating order. Results matched. Median query time was
84.34 ms without the index and 0.19 ms with it. These synthetic SQL measurements
are not whole-app, native collection, GPU, memory or battery measurements.

Task evidence: `outputs/messenger-recency-scheduling-20261025/` with
`policy-comparison.json`, `policy-traces.json`, `selection-query.sql`,
`selection-query-measurement.json`, `sensitivity.json` and regression logs.
Signed candidate/native execution, original production activation, installed UI
delivery, representative real latency and current-source external review are
separate acceptance stages. Existing Pro/Gemini/reverse review and source-transfer
holds are preserved.
