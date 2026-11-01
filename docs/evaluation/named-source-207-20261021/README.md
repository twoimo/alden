# Named-source candidate on runtime 207 dependencies

This is an uninstalled retrieval experiment, pinned to runtime `56eef4f87538fc9dbb95e18401ea4d82390a8143` (Alden 0.3.207). Production retrieval, candidate and patch bytes are unchanged from the [204 artifact](../named-source-20261021/README.md). The manifest updates the source-text and graph-title helper dependencies; the earlier artifact is retained as historical evidence and correctly rejects changed dependencies. This directory contains no private corpus, query vectors or application state.

The intended outcome remains finding a retained source by its name while preserving complete-body grounding, exact versions, permissions, cancellation and publication selection. A title match is source lookup, not proof that the body answers the query or that the source is true. The proposed ranking policy has not been installed or promoted.

## Reproduce the controlled checks

From the repository root:

```sh
"/Users/twoimo/Library/Application Support/openkakao/runtimes/menubar/bin/python3.11" -B docs/evaluation/named-source-207-20261021/run_fixtures.py
```

The runner verifies source, dependency, fixture and patch hashes, prepares the candidate in a temporary directory, runs 53 controlled tests, and confirms repository source is unchanged. All 53 passed on the 207 dependency set. No live application data, model inference or production writes are part of these fixtures.

Production retrieval SHA-256 is `51bfb4fa29c67ad5863345d4342e98c485d7baec504412bb5b7e7468bfb4a938`; candidate SHA-256 is `229d7bdd2dd1ede7da9dc05ddcfa0bc7ac4aace575ba1b6823f8a0e2db27d594`. `source-manifest.json` binds all dependencies and the exact patch.

## Separate actual-source comparison on 206

The preceding comparison used source revision `ea64dfba4e6b163a2aadef01e871888ee724b019`, runtime 0.3.206 and frozen read-only source/index transactions. It is not a rerun on 207.

An initial saved-title protocol required at least eight new positive cases, excluding the original six probes. All five permitted scopes were text/vector ready, but only six cases qualified. That gate failed before inference; its threshold was preserved.

A separate displayed-heading protocol preselected nine new positives across three scopes: five saved titles and four headings derived by the display helper. It also included three absent-token negatives and the original control. One scope contained only one document. This is a mechanical source-lookup probe, not independent human semantic relevance gold or a sufficiently broad holdout.

Both methods used the same 13 query vectors and frozen source versions; method order alternated, with 26 actual RRF calls. The existing ready E5 model served successful batches of eight and five. A preceding 13-item request was rejected for exceeding the server's maximum batch size of eight; that evaluation-harness mistake and its log are retained. No provider or model substitution occurred.

| Measure | Production | Candidate |
| --- | ---: | ---: |
| New positive cases | 9 | 9 |
| Expected source in first five | 5 | 5 |
| Sum of reciprocal ranks | 5.3742424 | 5.3742424 |
| Original control rank | 24 | 1 |
| False named-title promotions in three negatives | 0 | 0 |

There were no hit-at-five regressions in this sample. The negative check concerns false title promotion; dense results were still permitted, so it does not establish correct no-answer behavior. These results do not establish generalized relevance, latency, resource or quota improvements.

The four derived headings turned out to be export link fields. Both methods missed the expected sources in the first five, at ranks 10–12. That concrete counterexample led to the separate [207 graph-title fix](../../display-source-headings-20261021.md), which chooses literal source content after recognized export headers. Updating graph display titles does not itself repair retrieval ranking or establish useful semantic communities.

Private evidence is retained under task outputs `named-source-holdout206-20261021/`: the preselection contract, full-scope coverage, stable-source checks, per-case results, vectors, initial rejected request and corrected comparison. The public artifact contains only code, hashes and aggregates.

## Remaining decision gates

Retain the candidate for review. Before promotion, complete the applicable ChatGPT thesis → Gemini adversarial Deep Research → ChatGPT reverse verification, bind full reports to source and original URLs, and map findings to decisions and checks. One terminal thesis has human approval for local queue priority; the shared quota-admission hold still blocks dispatch. The approval does not verify model allowance or complete any external stage. Existing research and reservations remain intact.

Independent human labels must cover paraphrases, wrong/partial titles, ambiguous same-title sources, title-only references, temporal validity, revoked permissions and unsupported answers across multiple projects and source types. Source lookup, answer grounding, freshness and identity correctness need separate judgments. Representative matched latency/resource checks and installed MCP/UI verification are also required if this policy is selected. The production rank-24 failure and full 18-item objective remain open.
