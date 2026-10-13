# Source-bound tag comparison

The graph's explicit duplicate-candidate preview previously showed matched
stored text without the retained record's topic and entity tags. Two real
career records can have the same body and different tag declarations. The
comparison now displays each original's declarations and identifies their
differences so that a reviewer can inspect the original evidence.

## Contract and preservation

Only the separately requested original comparison reads annotations. The
aggregate audit remains counts-only. `topics` and `entityTags` come from each
already verified retained JSON record, with its current membership, version,
permission, projection and raw SHA checks. No neighboring record or graph
label supplies missing tags. The existing six-group/two-record preview limits
remain intact; the annotation read adds no source-path traversal or scan.

`retained-declared-tags-v1` explicitly reports `semantic_status: unverified`.
Missing fields, declared empty lists, unsupported formats and budget overflow
are distinct. Each field permits at most 32 strings of 128 Unicode code points.
Accepted strings preserve order, repetition, case and spacing. The default
entity-resolution read and merge fingerprints remain unchanged.

The UI validates the contract, copies only permitted fields and renders with
`textContent`. It distinguishes different declared values from order/repetition
differences and differing observation states. Its warning asks for original
review rather than determining which declaration is correct. This change
does not rewrite source text, tags, factual relations or entity identities.

## Actual source evidence and verification

The installed 0.3.104 comparison returned two matched-body groups/four originals
without annotations. The candidate returned the same document IDs, versions,
raw hashes and bodies with source-bound annotations. Recruitment records had
identical tags. A promotional pair had different topics and entity tags:
one declared `recruitment_timing` with `medical`, `samsung`, `school`; the other
declared `recruitment_timing`, `job_strategy`, `public_agency` with `construction`,
`gov`, `nh`, `public_corp`. These are retained declarations, not confirmed
entities or proven classification errors. The upstream cause remains open.

Two new checks fail against the fixed installed source because it lacks this
contract. Related candidate backend checks pass 79 cases; all 861 UI cases and
TypeScript checking pass. A production-panel DOM run with the actual response
shows two groups, eight annotation lines and one difference warning. Initial
minimal test markup omitted the real details summary, causing the correct
close/cancel guard to discard the response. That harness failure is retained;
matching the actual markup resolved it without weakening product guards.

The existing 1,984 verified vectors have 1,953 distinct vector hashes, with 25
repeated groups and no repeated-vector group spanning different text hashes.
This rules out an exact all-vector collapse in that page. It does not establish
semantic correctness, global nearest neighbors or identity equivalence.

Build, CI, native screens, installation, independent recovery and preservation
receipts are retained separately in the task's
`outputs/annotation-comparison-20261014/`. Single read timings use different warm
conditions and are not a speedup claim. Broader human semantic/identity/time
gold, upstream tag correction, physical interaction, representative resources
and signed/notarized production release remain separate open gates.
