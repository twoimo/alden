# Declared collection scheduler

Alden 0.3.41 includes a native, bounded producer for the existing registered source snapshots. It refreshes the projection, retains exact permitted originals, and runs the existing local search-index producer in each target's permitted project scopes. It does not discover accounts, borrow cookies, send messages, load models, or resume the emergency latch.

The separate scheduler SQLite store records attempts and partial results. One cycle owns a nonblocking process lock. Source publication retains the existing per-target lock and emergency commit guard. A pause that wins the publication lock preserves the checkpoint. Crashes and capture/index failures are recovered even after source storage has advanced the next-run checkpoint. Revision conflicts block only that target until an explicit target resume. Retry delays start at 60 seconds and grow to six hours. Idle checks add no empty cycle records.

The app-owned launchd job checks due targets every minute, with one target and a cooperative 180-second budget per invocation. Each target's saved interval governs collection. It uses the pinned Python 3.11, `-B`, Standard process policy, Nice10 and explicit bounded work, with no growing stdout log. Fresh active voice status defers collection and cancels further batches. An already pending local HTTP call must finish or time out before cancellation can be observed. Search checks the currently visible source version and hash, so incomplete/stale indexing never becomes a confirmed hybrid result.

Supported installed commands:

```sh
"$HOME/Library/Application Support/openkakao/runtimes/menubar/bin/python3.11" -B \
  /Applications/Alden.app/Contents/Resources/scripts/alden_collection_scheduler.py \
  --state-root "$HOME/Library/Application Support/openkakao/bujamentor" --status
```

Use the same arguments with `--install-schedule`, `--once`, `--pause`, or `--resume`. `--target <registered-id>` restricts a run or pauses/resumes that target. Scheduler resume does not resume the emergency latch. Installing an identical loaded definition reuses it; foreign/different definitions are preserved and reported. The job can run while the desktop window is closed and requires a logged-in launchd user session and the installed app/runtime.

Source verification: 69 collection/retrieval/snapshot/scheduler tests and eight build-receipt tests passed locally. Scheduler cases include real snapshot parsing/original capture/index production with fixture vectors, index failure, crash recovery, voice priority, permission exclusion, pause versus publication, exact schedule persistence and duplicate installation. These checks do not establish production acquisition, installed periodic execution, or physical voice behavior; installation readback is recorded separately.

# Build reuse

The canonical builder serializes builds and reuses an artifact only when source, locks, resources, installed npm bytes, actually compiled Rust dependency source bytes, toolchain/environment, CLI and all bundle files still match a completed receipt, including strict signature verification. Rust dep-info avoids downloading uncached foreign-target crates during cache checks. Missing dependency evidence uses the normal build path. No source provenance is rewritten and no targets, apps, backups or source data are deleted.

The initial unchanged-build probe took 0.681 seconds and preserved 51 bundle files, CLI and receipt bytes/mtimes. It preceded the expanded dependency audit; final repeated-build results must be recorded separately. No measured quota savings, storage reclamation or production performance improvement is claimed.

## First installed cycle and correction

The 0.3.39 app and 52 files were installed after all four CI jobs passed at source47190b0. The launchd job was saved and started. Its first 12,561-record career snapshot hit the cooperative180-second deadline before publication; the transaction rolled back, preserving the existing source checkpoint. The newly created collection scheduler was paused for this correction; the emergency latch and model/worker settings were preserved.

The FTS projection previously deleted each document by an UNINDEXED field, repeatedly scanning the26,622-row corpus. In0.3.40, final bodies are staged by document ID and the FTS table is updated once inside the same transaction. Duplicate IDs preserve the last body, unrelated rows remain intact, and later relation failures still roll back search/data/events/checkpoint together. No schema migration is needed.

A single consistent private recovery copy supplied500 selected rows, alternating old and batch SQL three times each. Median update time was2.530 seconds versus0.0467 seconds. All selected values matched and each trial rolled back. This measures only that FTS operation; installed whole-cycle time and search-index completion must be read separately.

Text extraction also avoids deleting nonexistent FTS rows for new versions. A processing-only revision reuses a prior vector only for the same document, exact text hash, model, endpoint and encoding, after checking the permitted retained source bytes. Reused versions are reported separately from actual embedding calls. Changed text or encoder requires new embeddings. Reuse inserts are committed in bounded batches; this does not fabricate inference success or merge document identities.

## Installed background policy investigation

The installed0.3.40 launchd job still hit180 seconds in collection despite the isolated FTS improvement. An actual installed-code foreground SQL profile, with Nice10 and a30-second overall budget, completed career storage/original capture and reached indexing. The prior Background/LowPriorityIO policy is therefore a concrete cause candidate, not a verified overall speedup.0.3.41 uses Standard/Nice10 with one target, voice deferral and the same180-second bound, then compares actual scheduled execution. Only the exact earlier owned definition can migrate, while unloaded, with a byte-verified private plist backup. Different scopes/arguments and active definitions remain preserved.

The private derived writer uses a bounded64MiB page-cache budget; foreground/original readers are unchanged. On the same private recovery clone with pinned Python3.11/SQLite3.46, full career SQL replay excluding raw-blob I/O took9.209s at2MiB and6.028/5.078s at64MiB, all rolled back. These are unequal small samples of SQL only; no whole-app gain or storage reclamation is asserted.
