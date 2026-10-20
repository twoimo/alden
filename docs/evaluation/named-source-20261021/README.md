# Named-source retrieval counterexample and candidate

Status: a locally tested, uninstalled candidate. Alden is now 0.3.204 with production retrieval SHA `51bfb4fa29c67ad5863345d4342e98c485d7baec504412bb5b7e7468bfb4a938`. The candidate is explicitly rebased onto runtime `b846c17884152fd163910f43c844a53bf99a94cd`; the historical 0.3.203 evaluation remains bound to commit `0adad7709b439d099dc6932119d19dc59f36e0e8` and its original private evidence. The named-source patch content is unchanged apart from hunk positions. This experiment does not change the database, source records, embeddings, identities, relations, model routing or the active search policy.

The intended outcome is that a user can find a retained source by its title without title-only catalog references crowding it out. A title match locates a source; it does not establish that its body answers the query or that its claims are true. Existing complete-body grounding, explicit pins, publication selection, permissions, cancellation and version/hash checks must remain effective.

## Observed failure and alternatives

The installed source query `제한시간 25분 초대왕 돈까스` finds its source at dense rank 2, outside the lexical top 40, and fused rank 24. It is consequently absent from the five returned entities. Giving the title field weight 4 admits it at lexical rank 40 and fused rank 12, still outside the returned set. This is an observed candidate-budget/fusion failure; it does not establish every cause of the underlying BM25 ordering or justify replacing the database.

SQLite's documented `bm25()` trailing arguments assign weights to columns. The comparator uses weights 0, 0, 4, 1 for the two unindexed identifiers, title and body. The value 4 is one declared diagnostic alternative, not a calibrated optimum. [SQLite FTS5 documentation](https://www.sqlite.org/fts5.html#the_bm25_function)

The second candidate preserves verified complete-body matches first. It then admits sources whose current title contains every original literal query word, whose body is present, and whose exact source/version/membership and content classification verify. Remaining results keep backend order. A title-selected proof says `source lookup, not body grounding`; references remain marked as references. It shares the existing 100 verification budget with body checks, rejects incomplete title matching beyond 24 original words, and bypasses this preference for explicit latest selection and dense-only fusion weights.

| Retained-source lookup | Current | Title weight 4 | Verified named-source candidate |
| --- | ---: | ---: | ---: |
| 제한시간 25분 초대왕 돈까스 | 24, absent from five returned | 12, absent | 1, returned |
| 4년만에 재도전 디진다 돈까스 | 1 | 1 | 1 |
| 왕돈가스 평생 무료 | 1 | 1 | 1 |
| 돈가스 도전 제한 25분 정정 | 1 | 1 | 1 |
| 도전 기록 13분 49초 | 1 | 1 | 1 |
| 초대왕 바지락 칼국수 | 1 | 1 | 1 |

Each rank is the first expected retained source or explicitly related source claim in the candidate list. These six cases were selected from three known videos and retained claims, including the reported failure. They are a narrow source-lookup probe, not independent human semantic gold, a holdout benchmark or evidence that the source claims are true. Title-only expected sources remain `empty_body` references. Candidate label lookup does not merge their identities.

The actual comparison used the existing 1,118-document permitted project. Every three-method query window had the same before/after `(id, version, text_hash)` signature. One existing warm E5 batch produced six query vectors; every method reused those vectors. The final hash-bound comparison reused them with zero additional embedding requests. All methods actually executed RRF; the field-weight alternative executed 12 weighted SQL statements. Source originals and result provenance were verified locally. Private source bytes, vectors and protocol evidence are excluded from this directory and all research inputs.

Per-request durations are retained as diagnostics. The run order was fixed, the corpus is narrow, and warm/cache effects are not controlled. No speedup, p95, whole-app or quota-saving claim follows from these timings.

## Reproduce the controlled candidate

From the repository root, use the pinned Python runtime with `-B`:

```sh
"/Users/twoimo/Library/Application Support/openkakao/runtimes/menubar/bin/python3.11" -B docs/evaluation/named-source-20261021/run_fixtures.py
```

The runner checks the baseline, dependency, fixture and patch hashes, applies only the candidate module to a temporary folder, runs the existing collection retrieval fixtures plus ten named-source behavioral cases, including the baseline canonical-query regressions, and verifies the repository source stayed unchanged. It requires the standard `patch` tool and Python standard library. No product data, live provider, model loading or sending is involved.

All 53 cases pass on the rebased dependencies (the prior 0.3.203 snapshot passed 50). The original normal-RRF source-order regression fails against production; its dense-only and explicit-pin checks still pass. Coverage includes complete-body precedence, partial title/body splits, metadata/link/empty-body classification, numeric boundaries, denied projects, revocation/revision/cancellation, shared verification limits, overlong-query rejection, stored-cache fallback, retained-original cache-integrity rejection and latest-publication selection. An early fixture incorrectly expected a rejected stored cache to crash, then inspected a historical cache row without its version key. Those diagnostics are preserved; the final fixture separately checks safe fallback and exact-current retained-cache rejection without relaxing the no-forged-content property.

The rebased candidate SHA is `229d7bdd2dd1ede7da9dc05ddcfa0bc7ac4aace575ba1b6823f8a0e2db27d594`; `source-manifest.json` binds its baseline and patch. The rerun six-query comparison used the installed 0.3.204 baseline, the rebased candidate, unchanged query vectors and stable source-version windows. Current private evidence is under task outputs `query-nfc-20261021/rebased204/`; the earlier `retrieval-title-evidence-20261021/final/` artifacts are preserved.

## Remaining decision and delivery gates

Local decision: retain this candidate for review; do not adopt the sampled title weight as a general setting. No production preference is changed here.

The shared research coordinator reports another owner's FIFO turn ahead, no ChatGPT slot, and no currently eligible verified account for this dispatch. The existing maturity/Graphify/terminal/DB requests and original conversations remain intact. Preferred ChatGPT thesis → Gemini adversarial Deep Research → ChatGPT reverse verification of the applicable source delta is incomplete. This local comparison is not a replacement for those reports.

Next, attach this exact public revision/patch and aggregate comparison to the applicable admitted review after verifying source access and account/model/research/quota. Ask the reviewers to challenge title-driven promotion, adversarial labels, body-grounding confusion, candidate truncation, budget exhaustion, multi-source ambiguity and current-version lifecycle. Preserve full reports, source hashes and original URLs, and map each finding to a decision, implementation condition and test.

Before product promotion, evaluate an independently labeled query holdout across multiple projects and source types, including paraphrases, partial/incorrect titles, ambiguous same-title sources, title-only references, stale/retracted material and no-supported-answer cases. Check retrieval relevance and citation/freshness/permission errors separately; a case that finds a source does not prove a correct answer. Measure representative latency and resource costs with matched source versions and randomized/interleaved order.

If selected, apply the reviewed patch to product source, run affected regressions, build/sign/install, and exercise the original query through installed MCP and the actual popup/source/version/activity path. Preserve settings and collection schedules, confirm runtime hashes and CI, and retain the verified installation recovery point. Until then the reported production ranking defect and the full 18-item objective remain open.
