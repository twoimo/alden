# Retained context through the voice boundary — uninstalled candidate

The preceding matched-context candidate can preserve source coordinates and
matching passages in retrieval but lose both benefits in the existing voice
consumer. Two mechanical counterexamples show the consumer dropping
`body_excerpt` and truncating away later matching words at its600-character cap.
These failures concern integration of an uninstalled candidate; the deployed
NFC intent/refusal correction is a separate change.

This review-only delta applies after
`../matched-context-207-20261021/candidate.patch`. It supplies source-body ranges
for ordinary content records whose complete body already fits the retrieval
budget, and preserves body/label excerpt metadata through the consumer. Long
annotated facts are reduced with the existing bounded literal-window helper.
`fact_projection` binds exact codepoint ranges and a UTF-8 hash of the retrieved
fact before voice compaction. `body_excerpt` continues to describe the verified
source body. The two coordinate systems are explicitly distinguished in input.
Ordinary unannotated facts retain their prefix contract. Query intent, numeric
boundaries, permissions, versions, cancellation and input budgets retain their
existing contracts.

The600-character fact cap and8,000-character encoded input cap remain. More
metadata may cause complete facts to be dropped by that existing input budget;
literal coverage does not prove surrounding conditions or semantic completeness.
Neither coordinates nor preserved query words establish truth, independent
relevance, identity or answer quality. This candidate is not installed.

The fixture suite passes97 cases, including the existing collection retrieval,
named-source, matching-context and voice suites. New cases cover lost metadata,
discontinuous ranges, exact codepoint/hash reconstruction, preserved default
prefixes, decomposed requests, emoji/escaped text and short retrieved bodies
whose late match would otherwise be cut at the voice boundary. The first
reproduction harness named a nonexistent embedding helper; its error and the
corrected explicit offline adapter are retained privately. Source inputs and
prior patches are hash-checked before execution.

Run `python3 -B docs/evaluation/context-consumer-210-20261021/run_fixtures.py`.
`--baseline` keeps the previous retrieval candidate and unchanged consumer and
is expected to fail the two original integration cases. Temporary modules are
removed when the run finishes; runtime sources and the installed app are not
edited. `source-manifest.json` binds the exact source/dependency snapshot and
parent revision. Private evidence is under
`outputs/context-consumer-210-20261021/`.

Promotion still requires the original ChatGPT thesis → Gemini adversarial Deep
Research → ChatGPT reverse verification, independent false-promotion/semantic
holdouts, downstream context/answer evaluation and representative resource
checks. The approved original research retains its model-allowance hold. Passing
fixtures or preparing this delta does not complete those gates.
