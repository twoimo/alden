# Bounded pin-neighbor lookup — 2026-10-27

The existing sparse collision path can now index its admitted cells in a reusable 32³ array when every occupied axis is a safe integer in [-15,14]. It keeps the sparse coordinate frame, admitted population, traversal order, samples, quotas and arithmetic. Other cells use the original string-key Map lookup. This does not enable the separate dense solver for pins or change source identities, geometry rules or persisted checkpoints.

Source baseline: `fd18285ceb920c70031ee9acd9a13e7716180eb5`, solver SHA-256 `be1d36c3fa81f0ab7b0bc3caa59dac51687414ef173999971e6bc51c0d402d8d`. Candidate solver SHA-256: `956d8dbcd9ddb7fdf26d87b8237c7b7990325f4acaa7bfd90cdc8bf0e8cb28ac`. Helper SHA-256: `c3f321b2cc4ab84c8082d1a14e4521f37ba1a27e5e64be1f389028d76af46502`.

## Index safety and preserved behavior

The witness is rebuilt after sparse cell admission on every solver iteration. An occupied integer axis in [-15,14] plus a neighbor offset in [-1,1] lies in [-16,15]. Adding 16 gives a base-32 digit in [0,31]; `dx*1024+dy*32+dz` is therefore unique and lies in [0,32767]. Every intermediate is an exactly representable JavaScript integer. Checking only the flattened index would permit carries between axes; all three axes are checked instead. Negative zero addresses the same cell as its canonical string key.

The caller constructs each cell's string key from those same coordinates and retains the original cell object. The indexed lookup consequently returns the same object or undefined at each position in the unchanged 27-neighbor loop. Samples, cumulative lengths, centroid addition order, density and force calculations remain in the same order. Duplicate coordinates reject indexing. Successful rebuilds clear all slots; a null return requires the original lookup and its buffer contents are never used. The local buffer has 32768 slots and is not persisted or shared across layouts. Extreme, fractional, unsafe or out-of-domain coordinates do not get clamped into the grid.

This is an argument about the validated integer domain and this concrete caller, not a whole-program formal proof. The independent tests and differential executions below provide separate evidence.

## Matched actual-input experiment

The input is the frozen permitted original graph captured for the [actual graph/event CPU baseline](graph-actual-event-cost-20261027.md). It is replayed locally; labels, original IDs, URLs, project scope, positions and source packets are not attached to external review. This is not a fresh source-authority check or a corpus export.

Both engines restore a deep copy of the same pre-pin checkpoint, use the same induced view, first pin, unchanged initial anchors, empty affinity input and frozen observation clock. Restore/copy and full-result comparisons are outside the timing interval. Per size, two balanced warmup blocks precede eight alternating ABBA/BAAB blocks: 16 measured calls per implementation. The 120 warmup/measured calls and three initial comparisons all retain exact full results, diagnostics, Map/Set order and engine checkpoints; inputs and the pinned position remain unchanged.

Node v26.12.0, macOS arm64, shared local host. Times are pure first-pin solver intervals:

| Actual nodes | Relationship groups | Baseline median, ms | Candidate median, ms | Reduction in medians | Paired-block mean reduction range |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 128 | 514 | 11.572 | 4.938 | 57.3% | 50.1–73.7% |
| 512 | 1967 | 39.451 | 21.222 | 46.2% | 26.3–51.8% |
| 1984 | 2693 | 113.130 | 53.222 | 53.0% | 29.9–63.8% |

All eight blocks favor the candidate at each size. Balanced order reduces but does not remove JIT, GC and shared-host variation. Empirical p95 from 16 samples is not a population estimate. The process resource snapshot covers both implementations and is not a per-implementation memory saving. These results do not establish faster initial layout, other graphs or pin positions, Worker transport, UI input latency, GPU, battery, whole-app speed or an asymptotic improvement.

## Independent and native checks

- Native Codex `gpt-6-astra` / `ultra`: 103 new helper tests pass against an independent coordinate-string oracle, including bounds/halos, non-numbers, fractions/extremes, alias/carry rejection, negative zero, duplicate coordinates, unchanged objects and reused-buffer cleanup. Eight defective controlled variants are rejected; loosening occupied bounds to [-16,15] produces seven failures. The original solver never exported this helper; the fallback-only adapter is explicitly a control, not a historical API claim.
- The same independent worker's 13 engine checks include 12 state traces and 56 complete result/checkpoint comparisons: cached/reflow/restore, first/moved/released/multiple pins, signed extreme magnitudes, 1024 coincident nodes and fallback transitions. Observations saw 1016 indexed dispatches and eight original-path fallbacks, so both branches are exercised. All diagnostics/budgets match. These are controlled fixtures, not real UI gestures.
- The existing ten layout, worker, driver, contact, center and small-weight test files pass 154 tests. Full desktop TypeScript `--noEmit` and Vite build pass, with build/cache outputs isolated from the primary dist. The new 103 tests and fixture traces are separate denominators; no full-repository test claim follows from their sum.
- Supplemental OpenCodex `google-antigravity/gemini-4-argon`, high, request `ocx-e836124bae23d0d82cc2a52a9cb574a9`: one canonical upstream send, no recovery. It substantively read the supplied helper, caller and test code, found no concrete P1/P2 defect and accepted the stated arithmetic and qualified reduction calculation. It did not execute the harness or inspect the private graph. This is not the held formal web-Astra/Gemini/reverse audit.

Task artifacts, raw measurements and preserved controls are in `outputs/graph-pin-neighborhood-20261027` outside the repository. The worker's original ignored evidence directory remains in the writer and is independently copied there; checksums include the applicable public source files. Source/test review and component execution are not installed/native/rendered acceptance.

The full original 18 requirements, follow-ups, ST01–16 and Cursor objective remain active. Alden stays OFF. No model server, account, permission, SDK, queue, capacity, installation or release state is changed by this candidate. Required external validation and installed UI/GPU/voice/event/semantic gates remain incomplete; source/CI/static packaging must not be promoted into those claims.
