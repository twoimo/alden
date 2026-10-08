# Declared collection scheduler

Alden 0.3.39 adds a native, bounded producer for the existing registered source snapshots. It refreshes the projection, retains exact permitted originals, and runs the existing local search-index producer in each target's permitted project scopes. It does not discover accounts, borrow cookies, send messages, load models, or resume the emergency latch.

The separate scheduler SQLite store records attempts and partial results. One cycle owns a nonblocking process lock. Source publication retains the existing per-target lock and emergency commit guard. A pause that wins the publication lock preserves the checkpoint. Crashes and capture/index failures are recovered even after source storage has advanced the next-run checkpoint. Revision conflicts block only that target until an explicit target resume. Retry delays start at 60 seconds and grow to six hours. Idle checks add no empty cycle records.

The app-owned launchd job checks due targets every minute, with one target and a cooperative 180-second budget per invocation. Each target's saved interval governs collection. It uses the pinned Python 3.11, `-B`, background priority and low-priority I/O, with no growing stdout log. Fresh active voice status defers collection and cancels further batches. An already pending local HTTP call must finish or time out before cancellation can be observed. Search checks the currently visible source version and hash, so incomplete/stale indexing never becomes a confirmed hybrid result.

Supported installed commands:

```sh
"$HOME/Library/Application Support/openkakao/runtimes/menubar/bin/python3.11" -B \
  /Applications/Alden.app/Contents/Resources/scripts/alden_collection_scheduler.py \
  --state-root "$HOME/Library/Application Support/openkakao/bujamentor" --status
```

Use the same arguments with `--install-schedule`, `--once`, `--pause`, or `--resume`. `--target <registered-id>` restricts a run or pauses/resumes that target. Scheduler resume does not resume the emergency latch. Installing an identical loaded definition reuses it; foreign/different definitions are preserved and reported. The job can run while the desktop window is closed and requires a logged-in launchd user session and the installed app/runtime.

Source verification: 65 collection/retrieval/snapshot/scheduler tests and seven build-receipt tests passed locally. Scheduler cases include real snapshot parsing/original capture/index production with fixture vectors, index failure, crash recovery, voice priority, permission exclusion, pause versus publication, exact schedule persistence and duplicate installation. These checks do not establish production acquisition, installed periodic execution, or physical voice behavior; installation readback is recorded separately.

# Build reuse

The canonical builder serializes builds and reuses an artifact only when source, locks, resources, installed npm bytes, actually compiled Rust dependency source bytes, toolchain/environment, CLI and all bundle files still match a completed receipt, including strict signature verification. Rust dep-info avoids downloading uncached foreign-target crates during cache checks. Missing dependency evidence uses the normal build path. No source provenance is rewritten and no targets, apps, backups or source data are deleted.

The initial unchanged-build probe took 0.681 seconds and preserved 51 bundle files, CLI and receipt bytes/mtimes. It preceded the expanded dependency audit; final repeated-build results must be recorded separately. No measured quota savings, storage reclamation or production performance improvement is claimed.
