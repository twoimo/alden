# Complete title words and matching retained passages — uninstalled candidate

Runtime 0.3.207 can omit a source even when the entire native title is supplied. Its existing body preference requires every query word to pass the Korean FTS helper; one-character Korean words and Latin/numeric words disable that preference. The preceding named-title candidate inherited this guard. Separately, the first long document can consume the entire 8,000-character context budget, and a returned document's prefix can omit the matching caption farther into its retained body.

This candidate separates complete title-word validation from recall filtering, preserves short Korean and Latin/numeric words, and shares the existing 100-source verification budget. Complete body grounding remains ahead of title lookup. A title match still explicitly means source lookup, not body grounding or independent truth. Permission, current-version, raw-source hash, searchable-text integrity, dense-only, explicit pin and temporal selection guards remain in place.

For ordinary relevance queries, selected entities share the existing context budget. Long content-bearing sources emit exact retained body slices around bounded literal occurrences. Every excerpt declares its normalized searchable-body SHA-256, total character count, Unicode codepoint ranges and truncation. Discontinuous slices use an explicit omission separator. Neither source bytes nor canonical identities, graph relations, embeddings or the total context budget are changed. Explicit pinned/time-filtered/latest queries preserve the original prefix contract.

## Paired evidence

The query contract was fixed before the original installed MCP results: eight native titles, eight caption spans and one previous control. The candidate uses those same 17 queries, their saved E5 query vectors and shared read snapshots with the installed baseline dependencies. Method order alternates. All 34 retrieval calls execute RRF; new embedding calls are zero. Private titles, captions, IDs, vectors and results are retained locally, not included here.

| Measure | Installed baseline | Candidate |
| --- | ---: | ---: |
| Expected native-title source emitted | 0/8 | 8/8 |
| Expected caption source emitted | 6/8 | 8/8 |
| Expected caption fact includes all original query words | 4/8 | 8/8 |
| Original control source emitted | No; candidate rank 21 | Yes; rank 1 |

The candidate has no source-return regression in this narrow sample. All 44 emitted excerpt checks match the verified body hash, version, scope and exact ordered source coordinates, and all responses stay within 8,000 characters. The fixture runner passes 63 tests: 43 existing retrieval tests, ten preceding named-source tests and ten new context/title tests. The installed baseline fails the new two-long-source reproduction because only one entity is emitted. These are local integration/mechanical probes with controlled fixture adapters, not an independent human semantic gold set or a performance, quota, answer-accuracy or production promotion result.

## Decisions and delivery gates

| Finding | Decision | Implementation / evidence | Remaining gate |
| --- | --- | --- | --- |
| Complete title validation must not silently discard original short/Latin/numeric words | Conditional candidate | `_original_word_plans`; exact title/body distinction, numeric-boundary and total verification-budget fixtures | Independent false-promotion/alias/identity gold and external delta audit |
| First-source context starvation and prefix-only matching failures | Conditional candidate | Fair entity budgets and retained excerpt ranges; long-source regression and 44 actual excerpt checks | Representative downstream answer and context-allocation evaluation |
| Passage matching and fairness may trade surrounding context for literal coverage | Unverified for promotion | No runtime activation; candidate exists only as a fenced patch | Independent review and broader representative fixtures, including facts whose meaning depends on omitted context |
| Bounded occurrence search may miss a better later passage | Explicit limitation | At most 128 accepted / 512 scanned occurrences per word; maximum 24 words and 64 characters per word | Representative long-document CPU/RAM/p95 and passage-quality measurements; no speed claim |
| Required ChatGPT → Gemini → ChatGPT review | Incomplete | Existing approved terminal204 request and original research checkpoints preserved | Original shared model-allowance hold reconciliation, then actual report chain and material delta review |

No runtime file or installation is changed. The installed app remains 0.3.207. This reversible candidate's dependent external validation and release remain blocked; a passing fixture or narrow live-source probe does not lift those gates. The full 18-item goal, ST01–13 and Cursor gates remain active.

Reproduce the isolated candidate with `python3 -B docs/evaluation/matched-context-207-20261021/run_fixtures.py`. The runner applies `candidate.patch` to a temporary module and verifies source/dependency/fixture hashes. `--baseline` runs the new starvation reproduction against unchanged source and is expected to fail. Public source-manifest hashes bind the original private query contract and saved vectors without publishing their contents. Private evidence is in `outputs/matched-context-207-20261021/`.
