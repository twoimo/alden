# Caption refresh: source lookup and measurement lifetime

This reversible source candidate continues ST16: reveal saved subjects only
after zooming in, keep hover/selection accessible, and analyze growing-data
costs. Baseline: `07d479faf1b5eb5ec95d6b7ce69aa7041bb1f0c5`. It is not installed;
the full 18-item goal and current-source external review gates remain open.

## Reproduced problem and correction

`KnowledgeHologram.refreshOverviewLabels` previously searched all displayed
nodes independently for each new caption. With N displayed nodes and B new
captions this takes O(BN) ID comparisons. The new implementation resolves just
the missing IDs in one pass, preserving wanted order and the first matching
record. It holds at most B temporary records, without a retained N-node index.
With no new caption it performs no source scan. Priority sorting remains
O(B log B); string handling, DOM creation and actual layout costs are separate.

`prioritizeOverviewLabels` also discarded every measured rectangle when hover
or the selected caption set changed. The new regression first failed on the
baseline: an unchanged second caption was measured twice instead of once.
Rectangles now follow caption identity through removals and priority changes.
New captions start unmeasured; existing font/size/wrapping/mode/resize checks
still invalidate the appropriate measurements. This changes cache lifetime,
not label membership, saved text, graph geometry, provenance or ranking.

## Matched synthetic measurement

Run from `desktop`:

```sh
npm run bench:label-refresh -- 07d479faf1b5eb5ec95d6b7ce69aa7041bb1f0c5
```

The maintained runner builds the actual baseline and candidate refresh methods
with identical unchanged dependencies. It uses happy-dom, instruments source
ID access, verifies exact caption output, warms four iterations, and alternates
11 batches of 40 refreshes per version and size. Node/runtime details, source
hashes and raw batches are in `outputs/label-refresh-20261023/benchmark.json`.

| Nodes / new captions | ID reads before → after | Median before → after |
| --- | ---: | ---: |
| 128 / 24 | 2,796 → 176 | 0.4945 → 0.4472 ms |
| 512 / 24 | 12,012 → 560 | 0.7292 → 0.3731 ms |
| 2,048 / 24 | 48,876 → 2,096 | 2.0648 → 0.5208 ms |

The fixture deliberately requests the final 24 IDs in reverse order. It tests
the costly lookup case, not a representative user distribution. The 2,048-node
median decreases 74.8% in this synthetic method workload; it does not measure
browser layout, installed-app FPS, GPU, RSS, source arrival latency or battery.
Scratch storage is O(B); no measured whole-app RAM saving is claimed. The
caption projection/sort and semantic solver retain their separately documented
costs and worst cases.

## Verification and delivery limits

- Full UI suite: 1,117 checks in 100 files passed with four workers. TypeScript
  and Vite build passed; the existing large-chunk warning remains.
- Three new regressions cover unchanged measurements after hover, identity-safe
  rectangle reordering with font invalidation, and the bounded source traversal.
  An initial test-fixture failure trying to redefine a nonconfigurable getter
  is retained separately; the fixture was corrected, not the assertion weakened.
- Compiled before/after renderers on the same retained 2,048-node snapshot:
  overview 0 passive labels, 1.7× 14, 2.3× 21, reverse overview 0. Both versions
  had 0 label/overlay overlaps and 0 clipping at these views. Source data stayed
  unchanged. Screenshots were directly inspected in hidden in-app-browser tabs.
  This is stored-data browser rendering, not installation or new collection.
- All QA stayed in background/headless surfaces. Native app observation failed
  with `-10005`; physical minimization is unverified. No restart followed it.
- Existing Pro reverse review remains queued behind shared service admissions;
  local coordinator state is not fresh provider availability. The requested
  `chatgpt-web/gpt-6-astra` is not advertised by the current agent tool, so no
  different model was dispatched. No current-source external validation or
  installed delivery is claimed.

The installation hold follows the shared pstack instruction that unresolved
dependent validation/release stays blocked. Independent source checks and the
normal PR/CI path continue. Existing installed runtime, collection settings,
processes and data remain protected.
