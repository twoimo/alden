# Canonical CLI and actual-source GBrain evaluation

Product source: `10f47c5bddf3e8c80978ceaddb2bcef55644d4e5`. This is a bounded local evaluation; the complete Alden goal remains active.

## Observable changes

`alden-cli` is the canonical Cargo package/binary, help/completion name, and internal executable resource. The regular `openkakao-cli` compatibility binary remains for independently deployed callers and frozen consumers. Both bundled resources are validated as regular executables. The Rust library namespace, persisted data/credential identifiers, app identity and fixed permission-sensitive legacy staging path are preserved. No installed application, permission database or service was changed.

GBrain readback now compares its publisher-normalized title while preserving the original label, source version, payload and record hash. It verifies page type/body/timeline/tags and opaque revisions. Every outgoing set, including small sets, receives exact endpoint/context/provenance checks. Canonical raw JSON comparison rejects boolean/number substitutions. For a small long-context set, bounded listing provenance and full traversal context are both required; high-degree contexts beyond the supported listing bound remain rejected.

## Actual original-source execution

The declared five-project, 43-target collection was read through the existing query-only reader. No original operational database was written and no collector/model was restarted. Private records, labels, archives and raw replies remain local.

| Evidence | Result |
| --- | --- |
| Complete source scope | 26,651 source nodes; 48,355 observed groups/claims |
| Complete engine projection | 26,694 pages, including 43 origins; 75,006 links, including 26,651 memberships |
| Full build | Every page enumerated; every page revision/record and outgoing provenance set verified |
| High-degree boundary | 9,436 outgoing links independently matched using page limit 317 |
| Bounded diagnostic queries | Five fixture selections in the authorized combined graph matched exact snapshot binding; this is not project-isolation or semantic-gold evidence |
| Refusal behavior | Bounded high-degree traversal rejected; normal reader refused unpublished generation |
| Actual subsequent engine delta | Six pages updated; six old links removed and six replacements added; zero page purges |
| Delta readback | All 26,694 pages and 75,006 links verified again; independent high-degree read used page limit 499 |
| Separate full-index rebuild | All logical page fields and all link fields matched the delta index in both directions; opaque revisions were excluded only from this logical comparison and were separately checked against the engine |

Both complete runs were **unpublished as-of proofs**. The original collector kept advancing. The unchanged strict source-current check returned `collection_export_stale`; no ready receipt, active pointer or global registration was created. These results do not establish live publication, installed UI behavior, native account binding or semantic truth. Export, index work, closed-store copy/hash and complete readback remain full scans; no O(delta), latency, memory, token or quota gain is claimed.

## Failure recovery and independent local review

An initial complete import exposed publisher title trimming and was rejected. A later stage driver lost transient `index_updates` after reopening a projection; its delta trial was rejected before acceptance and its derived cache was withdrawn. Those failures are excluded from accepted results. A fresh full build, diagnostic queries and corrected actual delta were completed afterward.

The task driver now saves those statistics before reopen, retains a hash-verified closed baseline, forces source-authority checks at acceptance, promotes unreaped readers to recovery holds, checks actual cancellation state and separates diagnostics from cleanup. Failed cleanup cannot skip the success-receipt invalidation attempt. Denial, withholding and source/code loss remain rejection paths; no source-current publication guard was relaxed.

Three user-authorized Codex-native `gpt-6-astra` workers independently built the candidate, reviewed lifecycle boundaries and ran a real native public/synthetic regression. The public trial verified four pages/five links, an actual graph query, changed-row delta, manifest reopen and receipt creation; its 19 owned children ended and its cache was withdrawn. Bounded mocked failure controls found and checked the driver fixes. Native review is separate from the required web-Astra and ChatGPT/Gemini/reverse review, which remain incomplete.

## Tests, artifact and limits

- Related pinned-Python checks: 477 plus seven metadata tests containing 44 metadata cases. CLI worker checks: 75. Independent merged-code review reran 39 existing tests and eight separate adversarial tests; these are not added to the related-test denominator.
- Product-source CI: [40273991193](https://github.com/twoimo/alden/actions/runs/40273991193), all four jobs successful at `10f47c5bddf3e8c80978ceaddb2bcef55644d4e5`.
- Build-only 0.3.220 candidate: tracked 2,024 files match that exact source revision. Normal build/check, three artifact receipts, 96 app files, 82 packaged Python sources, canonical/legacy independent inodes and identical bytes/signatures, identity-only and ZIP extraction bytes/modes/signatures passed. Cargo.lock stayed unchanged after normal build.
- Candidate ZIP: 22,487,282 bytes; SHA-256 `0e13127d65a8c92b11f42518f36e80e9e8a4c19a99a61fe148e4a0af2f8ac2be`.
- The app uses **Apple Development**, not Developer ID Application; the CLI uses linker ad-hoc signing. Static identity checks do not prove installed TCC continuity. There was no install, GUI launch, notarization or release.
- Fourteen previous app manifests and protected collector/model argument hashes matched. Three of five configuration hashes matched the original baseline; external drift was recorded without restoration. This task made no global configuration changes.

Reviewed GBrain v0.60.451.0 / `33d73cb54c14e2e988272764ddff59a5ead10915` and open-ontologies `ef6e781c71aaccaa35c2a1cd06169f0c862d5769` remain fixed. Ontology references inform source/proof separation; no OWL/SHACL/Lean/certificate engine was run. SDK reviewed-release integrity, native account/consent, source-current admission/catch-up, registered independent hosts/OSK, private gold, installed UI/3D/GPU/resources/inference/voice/tools, formal/external/worker acceptance and normal release remain open. Alden stays off; the original full objective is unchanged.
