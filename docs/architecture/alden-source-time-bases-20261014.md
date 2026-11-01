# Source date labels and collection-version times

An installed 0.3.108 answer called `source_author.date: 2026-02-17` a
collection date. The same original's version was stored at
`2026-10-11T12:25:13.554715+00:00`. The stored values were intact; their roles
were ambiguous in model input. Publication itself is not independently
verified by the retained date label.

## Bound per-original evidence

Each collected-source entity now carries `collection-time-bases-v1` evidence:
`collected_at_utc` is derived from that current version's `collected_at`, and
`source_date_label_basis` is `retained_label_publication_unverified` or
`not_declared`. Original `updated_at`, source date text, IDs, versions, hashes
and permissions remain unchanged. Matching sources each retain their own
storage time even when the exact body displays once.

The retrieval integrity check also binds the selected row's collection time
to the verified current version. Changed, missing, nonfinite or invalid
collection timestamps cannot silently become the host's current time. The
typed ledger validates evidence against that proof's time and date label;
this is structural consistency, not independent source authentication.
All-legacy proofs remain readable. New grouped proofs have evidence for every
member; mixed partial upgrades are rejected without rewriting old histories.

Model input names the three different roles: host query clock, retained source
date label and collection-version storage time. The host UTC clock is also
stated outside the source quotation. Current recruitment availability and
closure both remain unverified unless separately checked; a passed deadline
in a retained record is not a live employer-status lookup. Historical
questions keep the user's requested date.

The 8,000-character quote and 64KiB history limits remain. Partial/full budget
removal is explicit. No expiry, source deletion, identity merge, reindex,
current-validity promotion or model substitution is introduced.

## Verification scope

Related backend tests pass 166 cases, including differing per-original
collection times, old proof compatibility, forged evidence, missing timestamps
and a collection-time change after the selected-row snapshot. An initial
candidate helper reused an optional host-clock argument, accepting `None` as
now; a new regression exposed it and the final helper rejects it explicitly.

Actual saved Gemini/high typed-input probes distinguish the selected retained
date labels in February from October collection-version storage. Current and
past questions are checked separately with exact proof fields and private
history readback. The first historical probe's unsupported current-closure
statement is retained; the model rule was strengthened to distinguish source
deadline passage from unverified current employer state. These are selected
model responses, not broad human temporal gold or external employer evidence.

Web GPT-6/xhigh provided read-only code/contract review. Parent implementation,
tests, actual source reads and model calls are separate. Existing explicit
`time_from`/`time_to` filters use collection time, not occurrence time; text
historical probes do not apply those SQL bounds. Latest selection compares
stored declared observation dates, not independently verified publication or
supersession. These broader temporal/filter issues remain open, as do history
permission changes and retention. An intermediate prefix mismatch reported
by the reviewer was reconciled against final code and tests.

Task evidence: `outputs/source-time-bases-20261014/`. Build/install/native/CI
delivery receipts are distinct from physical UI/voice and semantic accuracy.
The full18 objective remains active; no quota or performance gain is claimed.
