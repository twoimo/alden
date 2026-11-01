# Alden 0.3.153: source-backed numeric reading and fact deduplication

Runtime source: `c3871f3aa203bebb9a8b0d5bba6b9f3fc908b0b8`.

The old reader hid trailing digit-only lines and certain inline fractions in every note/source. This could remove a year, measurements or identifiers without an extraction receipt. Its fact list also removed all numeric-only facts and applied the six-item limit before eliminating whitespace/NFC-equivalent display duplicates and blank strings. Seven prepatch regression cases reproduce these problems.

The reader now preserves general numeric content and facts. It deduplicates only NFC/whitespace-equivalent display keys, preserves the first displayed spelling, retains case/unit differences and applies the six-fact limit after skipping blanks/duplicates. It does not merge source records or identities.

The existing source-specific9 structural-tail candidates still use the same extraction policy and text-version stamp. The user's original selected post remains477 prose characters plus13 retained characters, exactly reconstructing490 characters and all five numbered sections.

For other legacy Threads exports, the backend may supply an optional `reading_projection` after verifying retained bytes and the explicit platform/type/origin/canonical-post identity. This affects presentation only: `details.body`, source versions, search text, embeddings and storage are unchanged. The UI accepts exact reading+tail reconstruction, the current selected version, bounded numeric shape and receipt fields; unrelated notes/sources remain intact. An adjacent collapsed disclosure retains the exact tail and explicitly says its meaning is unverified. A single year, a simple ratio, fenced code or oversized candidate is not folded. This is not a semantic outlier verdict.

A read-only current career audit verified12561 original hashes with zero integrity findings. It found9 existing structural candidates and35 same-projected-body groups; these remain review candidates rather than confirmed duplicates or identity. The generic UI classifier suppressed44 additional suffixes. Thirty meet the source-bound presentation rule;14 remain full. These counts neither prove30 noise items nor14 meaningful numerical claims. No source merge or index migration was performed.

UI983, Python254 distinct affected cases (16 source-text,139 collection,99 quality), Rust146 and strict Clippy passed. The original seven failures, the first source-only candidate, actual corpus receipts and native before/after evidence are retained under `outputs/normalization-review-20261019/`. The first task-owned scan accidentally opened a transaction inside the store's existing read transaction; it failed before reading records or writing anything, then the helper was corrected. It is not a product defect.

The [151 diagrams](architecture/alden-0.3.151/README.md) remain dated source-bound architecture evidence. This153 presentation delta does not certify external adversarial/reverse research, human semantic/identity gold, physical UI/voice/Retina, two independent global knowledge-MCP hosts, representative whole-app resources or public notarized production delivery. Full18goal remains active.

## Local delivery

Signed0.3.153 was installed from sourcec3871f3. [Exact-source CI39149262789](https://github.com/twoimo/alden/actions/runs/39149262789) passed all four jobs. All77 installed files/modes matched the canonical candidate; strict deep signature, unchanged designated requirement, independent152 recovery and protected settings plus seven target/acquisition policies reconciled. The primary launchd process runs the installed bundle. The existing foreign MLX process was observed without adoption or signaling.

The baseline/candidate/installed owned native protocols all passed. A real current source had420 displayed characters in152, with a five-character numeric suffix (including line breaks) silently excluded. In153 it still has420 prose characters, plus an explicit closed disclosure containing the exact five characters. Both viewport protocols show420+5=425, and the next selected source has no prior disclosure. The two affected installed reader screenshots and four candidate reader/schedule screenshots were inspected directly; other captures are not claimed directly reviewed. Compact notes use normal scrolling. Unit tests exercise expanding the disclosure; physical clicks remain separate.

The installed module APIs re-read all44 selected actual records. Current source versions/raw hashes and search text/version stamps match the before state;30 optional presentation candidates reconstruct the exact displayed body. The original user's477+13 source also remains exact. No index migration or model generation was needed. A private44-case review queue was prepared with source/version/hash and all decisions left unreviewed; it is not independent gold.

Final receipt: `outputs/normalization-review-20261019/verification-final.json`. Documentation delivery is separate from runtime installation.
