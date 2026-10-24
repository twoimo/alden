# Bare digit recall in collection search and graph pages

Base: `3407c08e8e3bdf14b378735bb7048e4398dbaede`. This source candidate fixes direct collection search and graph pages missing text such as `100개입니다` for the bare query `100`. Routed retrieval already had an additive digit-lexeme supplement; both paths now share that matcher. Original bodies, source records, IDs, memberships, versions, permissions, and FTS contents are preserved.

The policy accepts only a bare string of 2–64 ASCII digits. It adds complete matching digit runs after existing FTS results. It does not convert numbers, signs, units, grouped/decimal notation, Unicode digits, or identifiers into equivalent quantities or entities. Existing FTS punctuation matches remain compatible: a query can still return a token from `100,000` or `100.5`. Neither those existing hits nor new lexical candidates establish a numerical claim's truth. Quoted and advanced FTS queries keep their existing behavior. The existing `expand_korean=False` switch disables the direct-search supplement too.

## Selection and cost

For direct search, existing FTS results fill the requested limit first. If they already fill it, no body supplement executes. Otherwise, the supplement supplies additional eligible records after token hits. Both stages run in one source-guarded reader transaction.

For graph pages with offset O and limit L, the ordinary FTS page reads L+1 rows after O. If that page has a lookahead row, the supplement cannot affect its displayed rows or `next`: every FTS hit precedes every supplement hit. If no lookahead exists, a second selection includes the supplement. An exactly full displayed page without lookahead must still check the supplement; a regression covers that boundary. Each selection retains the same permitted/current membership, target/platform/type/relation/time filters and relationship integrity checks.

An initial candidate always scanned retained bodies. A subsequent SQL count gate still incurred unnecessary candidate materialization. The final candidate keeps the ordinary FTS graph SQL path when its lookahead suffices. The shared final SQL trace matched the baseline in the measured full-prefix graph case. Sparse searches can still require two graph-scope selections and a retained-text scan. This adds no numeric index or schema migration and is not an unbounded scaling claim. Display limits do not bound the amount of text a fallback might inspect.

## Verification

- Three new behavioral regressions failed on the base source before the fix.
- Four focused tests cover suffix/embedded digit recall, existing FTS ordering, quote/opt-out compatibility, pagination/lookahead, larger/grouped/decimal/Unicode runs, later occurrences, permitted current membership, time filters and byte/ID/version/FTS-count preservation. The previous Korean-only negative expectation was explicitly updated for the new integer supplement; one-character, Latin and oversized queries retain their negative checks.
- Final related Python checks: **313 tests in 10 fresh pinned-runtime processes**, all passed. This includes collection, retrieval, graph references, messenger access/context, quality, entity projection and content-evidence checks.
- Two actual retained-DB query cases used one normal read-only transaction per baseline/candidate pair. Source hashes were stable during each measurement. Existing selected IDs were retained, membership snapshot hashes matched, SQLite total_changes was 0, and no write was attempted. These cases exercised a full FTS prefix and a missing-number fallback; neither demonstrated additional matches in that live corpus. Positive recall improvement is demonstrated by the isolated regressions.
- These are source/SQLite observations. No new installed runtime, native search interaction, ingestion event, semantic gold, CPU/GPU/battery improvement distribution, or external model audit is attested.

Rerun the focused contract from the checkout with the pinned Python:

```sh
"/Users/twoimo/Library/Application Support/openkakao/runtimes/menubar/bin/python3.11" -B -m unittest discover -s tests -p test_alden_korean_search.py
```

Private detailed measurements and failure receipts remain under the task's `outputs/numeric-recall-20261024/`; they are not included in the product source commit.

## Integration boundary

This candidate is separate from the frozen installed-runtime-to-3407c08 review packet. That packet and its two already approved files retain their original bytes. Current-source external thesis/adversarial/reverse review and installation verification remain incomplete. This follow-up must be included in the applicable subsequent source review and integration checks; the original packet's review cannot certify it by implication. Full ST15 numeric value/unit semantics, independent gold, direct installed search and the remaining Alden requirements stay open.
