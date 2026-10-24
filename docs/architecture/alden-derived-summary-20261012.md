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

All 312 current members of the 9 previously admitted text groups were checked
against immutable retained derived-record bytes, current IDs/versions and
projection hashes. They contain summary fields and no original body fields.
The three 100-member groups each carry one shared *declared* original-file hash;
the other six two-member groups carry different declared hashes. Those fields
are source declarations, not independent original-file byte verification.
No protected original path was followed and no entity/alias merge was applied.

The source-level and installed all-project actions retained 26,623 documents,
26,625 memberships and 48,355 relations. Body candidates changed 9 to 0, while
metadata/name/notice matches changed 654 to 663 groups. Description-only current
versions changed 6,270 to 7,615. Findings remain empty within the structural
checks performed. These
are classification results, not semantic outlier, no-duplicate or human-gold
claims. NFC/line endings, body spacing, original records, FTS and existing
vectors remain intact. The same frozen 18 IDs/versions and existing E5 vectors
produced 33 layout-only affinity candidates before the fix and none after it,
including the installed Resources evaluator. All 18 are explicitly excluded as
descriptions, with zero validation failures. This sample is not whole-graph
semantic accuracy or an original-file duplicate verdict.

An independent `chatgpt-web/gpt-6-sol`/`xhigh` agent reviewed the provided text
contract only and was closed. Its cache/binding, guard priority, whitespace and
cross-path risks are covered by regression checks. Its coverage-omission concern
does not justify inventing a source type from absent metadata; unknown evidence
and broader original-source review remain explicit limitations.

Evidence is in the task's `outputs/source-triage-20261012/`: the original
failing regression, before/after actions, nine read-only previews, all312
source bindings, agent receipt and tests. The first evidence-reader attempt
failed before reading candidates because it opened a transaction already owned
by CollectionStore; the corrected reader used that existing snapshot. The strict
standalone affinity path refused missing WAL sidecars before reading vectors.
The separate product-owned attested read transaction evaluator performed the
sample checks without weakening that guard, changing journal mode or loading
another model.

Python 180, UI 776, native 136, typechecking and strict Clippy passed. The first
full UI run had one existing geometry-test timeout; its focused rerun and the
final full run passed without changing its deadline. Source CI completed all
four jobs. Alden 0.3.82 was built from eb5ea67617a433802eef575fa37e41f01f3a90c7
and installed locally. Its 68 payload hashes/modes and strict ad-hoc signature
match the candidate; a separate 0.3.81 recovery copy matches the prior install.
Settings, seven targets, two acquisition policies, model listeners and selected
collection/retrieval/voice tables remained exact.

Candidate native verification completed 14 states/28 PNGs in 74,174 ms, with
both sizes observing affinity, layout, rest and a strict two-second render idle.
The first installed run completed the same states in 81,927 ms but did not
observe timely affinity or render idle in the default window; compact passed.
That failure is retained separately from aggregate audit completion. The fresh
installed check completed in 76,465 ms with the same source, guards and
deadlines. Both sizes observed affinity, layout, rest and a strict two-second
render idle. All 2,048 projected centers are visible with zero invalid/clipped
centers in the candidate and final installed captures. Four relevant PNGs per
run were directly inspected, including the first installed run; the other
workspace PNGs are not claimed as visually reviewed.

This change corrects evidence admission. It does not claim less projected
overlap, faster startup, lower whole-app resources or battery improvements.
Current compact central density and independent whole-graph geometry/semantic
evaluation remain open. Physical input and Retina device behavior are separate
from native owned-window captures.

The local ZIP verifies all 68 payloads, modes and CRC. The selected build path
has no Developer ID distribution identity or configured notarization environment;
Apple Development identity is distinct. No public tag or production deployment
was performed. Final screenshots, PR/CI, preservation and package receipts are
in the task evidence folder. The full 18-requirement goal,
semantic gold/outliers, temporal supersession, physical interaction/voice,
broader host/media paths, whole-app resources and signed production release
remain active.
