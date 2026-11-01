# Matched whole-pool graph refresh costs

The current source/index/engine delta implementation is measured at code commit
`5f38c55a0e76cfbb4a9be28b987da9e53992c9f5`, with the same reviewed actual
GBrain binary and pinned Python 3.11 runtime. This phase adds measurements and
their replay harness; product code is unchanged. The signed candidate remains
the 95-file 0.3.220 app built at that source commit. No new installation or GUI
run is implied by this report.

## Input and comparison

The complete previously frozen MIRACL Korean judged pool has 3,048 source nodes,
3,057 observed grade-bound relationships, one origin, 3,049 engine pages and
6,105 projected links. This is the judged pool, not the full 1.48M-document corpus
or private/native semantic gold.

Both independently initialized owned caches use the same copied source root,
project and stable projection policy. Each pair observes the same one-record
label revision. The full side performs supported normal `refresh` after that
source change, which rebuilds the full source archive, graph index and engine.
The delta side runs `refresh --incremental`. Three pairs execute in full/delta,
delta/full and full/delta order. Initial full population (70.6s and 74.4s) is
recorded separately and excluded from the comparison. Filesystem and code caches
are warm; no cache flush or protected workload suspension occurs.

| Metric, median of three trials | Full rebuild | Incremental | Reduction from medians |
| --- | ---: | ---: | ---: |
| Command elapsed time | 70.91s | 18.43s | 74.01% |
| Accounted command/child CPU time | 76.92s | 20.10s | 73.87% |
| Sampled sum of process-tree RSS peaks | 1,412.0 MiB | 987.9 MiB | 30.03% |

Elapsed-time ranges are 66.43–75.68s and 17.73–20.75s respectively. The adjacent
JSON retains each pair's order, load average, CPU, memory samples/counts, phase
timings, source/index/engine receipts and exact source pins. The reduction is
`100*(median_full-median_delta)/median_full`. Three pairs do not establish a
population percentile, tail bound, cold-start gain or asymptotic scaling law.

## Result and measurement checks

Every timed trial completes all 3,049 engine page/outgoing-proof queries. Each
pair has the same complete source-bound fingerprint and ordered logical index
digest, including every page payload/content and link payload/context. Delta
updates put one engine page, replace one membership link and retain 3,048
unchanged opaque page revisions. Full rebuilds retain zero opaque revisions.
The copied source preserves all 3,048 current body hashes and all 3,057 stored
relationship records; only the selected label differs at completion. Frozen
original files remain exact. This is a controlled metadata change, not a native
user-message arrival, a new semantic-quality result or a real publication event.

`wait4` supplies command CPU and native-unit maxrss. An owned nested-child CPU
loop targeted 0.4s and produced 0.430s of accounted CPU, confirming child
accounting on this host for that calibration. Native maxrss remains labeled in
the raw receipts and is not converted into a hardware-memory claim. The reported
memory comparison instead samples the owned command tree with `/bin/ps` every
0.25s and sums RSS in KiB. Shared pages are counted for each process and short
peaks can be missed. This is neither physical memory nor total system energy.
The controller's sampling cost is outside accounted command CPU; both modes use
the same monitor. Phase spans are inclusive and nested, so they are not summed.

The first measurement command used an unsupported `--rebuild` CLI option.
Argument parsing rejected it with exit 2 before cache/provider work. That input
is excluded; the two accepted initial stores and already committed controlled
edit were reconciled and reused with supported normal refresh. The rejected
receipt, initial harness hash and corrected/resumed harness hash are preserved.
The logical comparison also uses the actual index's `payload`/`context` fields.
No uncertain write was retried and no completed initialization was replayed.

## Reproduction and limits

The exact corrected harness is committed as
`gbrain-cost-20261026/run_paired_cost.py`. On this task's host it resolves the
existing source checkout, frozen public input and reviewed runtime from the
documented task layout. Run it in a fresh sibling directory under the task's
`outputs/`, with the pinned interpreter and bytecode disabled:

```sh
task_benchmark_dir="/Users/twoimo/Documents/Codex/2026-10-07/macos-ai-alden-ai-1-cpu/outputs/gbrain-cost-rerun-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -m 700 "$task_benchmark_dir"
cp "/Users/twoimo/Documents/Codex/2026-10-07/macos-ai-alden-ai-1-cpu/work/alden-explicit-references-20261025/docs/evaluation/gbrain-cost-20261026/run_paired_cost.py" "$task_benchmark_dir/"
PYTHONDONTWRITEBYTECODE=1 "/Users/twoimo/Library/Application Support/openkakao/runtimes/menubar/bin/python3.11" -B "$task_benchmark_dir/run_paired_cost.py"
```

The script creates only its owned copied source and derived stores, records
individual process handles/results, verifies complete equivalence, and withdraws
both caches at completion. Resume mode is only for the documented terminal
input rejection and must not be used to replay an unknown live operation.

Whole-source authorization/comparison, index regeneration, temporary seen keys,
DB copy/hash and full engine readback remain. They still cover the store even
when logical mutations are selective. No end-to-end O(delta), token/quota,
battery, GPU/FPS, conversational latency or native-service gain is claimed.
Active model/collector workloads and AC power are retained; their executions are
not a fixed laboratory load. Thirteen prior apps, five config/profile entries
and both protected process argument hashes remain exact. Final desktop/owned
engine counts and remaining derived generations are zero.

Actual native account/device/collector integration, independent-host and private
gold, installed UI/GPU/lifecycle acceptance, formal proof, external
ChatGPT/Gemini/reverse review and normal release remain open. The unavailable
required web-Astra/max worker is not substituted. Alden stays off at the user's
request, and the full original18/follow-up/ST01–16/Cursor goal remains active.

Task evidence: `outputs/gbrain-cost-20261025T235218Z/` contains raw process,
RSS, phase, calibration, recovery, equivalence, source-data and preservation
receipts. The adjacent JSON carries exact hashes and all six measured trials.
