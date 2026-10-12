# Alden 0.3.82: derived summaries and retained body evidence

The Spark adapter reads the existing privacy-filtered derived SQLite index. It
copies a node's summary into searchable text and explicitly declares that
original bytes were not opened. Policy v3 treated most such summaries as body
candidates. This could count repeated README names or video titles as body
matches and admit their vectors as original-content layout evidence.

Policy v4 classifies that exact declared summary coverage as
`metadata_description`. Quality, resolution preview and semantic affinity
share the rule. A retained-body exception requires the caller's verified raw
hash and actual `localOriginalText` binding. Manual quality reads no source
bytes and cannot grant that exception from a disposable cache stamp. Existing
metadata-only and unread-notice exclusions retain priority. Unknown declarations
remain review candidates, never original-content or entity certification; no
short-text, equal-title, filename or inferred identity exclusion was added.

All312 current members of the9 previously admitted text groups were checked
against immutable retained derived-record bytes, current IDs/versions and
projection hashes. They contain summary fields and no original body fields.
The three100-member groups each carry one shared *declared* original-file hash;
the other six two-member groups carry different declared hashes. Those fields
are source declarations, not independent original-file byte verification.
No protected original path was followed and no entity/alias merge was applied.

The source-level all-project action retained26,623 documents,26,625 memberships
and48,355 relations. Body candidates changed9 to0, while metadata/name/notice
matches changed654 to663 groups. Description-only current versions changed6,270
to7,615. Findings remain empty within the structural checks performed. These
are classification results, not semantic outlier, no-duplicate or human-gold
claims. NFC/line endings, body spacing, original records, FTS and existing
vectors remain intact.

An independent `chatgpt-web/gpt-6-sol`/`xhigh` agent reviewed the provided text
contract only and was closed. Its cache/binding, guard priority, whitespace and
cross-path risks are covered by regression checks. Its coverage-omission concern
does not justify inventing a source type from absent metadata; unknown evidence
and broader original-source review remain explicit limitations.

Evidence is in the task's `outputs/source-triage-20261012/`: the original
failing regression, before/after actions, nine read-only previews, all312
source bindings, agent receipt and tests. The first evidence-reader attempt
failed before reading candidates because it opened a transaction already owned
by CollectionStore; the corrected reader used that existing snapshot.

Installation, native screenshots, CI and package receipts are separate delivery
gates and will be recorded after validation. The full18-requirement goal,
semantic gold/outliers, temporal supersession, physical interaction/voice,
broader host/media paths, whole-app resources and signed production release
remain active.
