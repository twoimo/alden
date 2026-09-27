# Wake detector state reset and paired evaluation — 2026-09-27

The release evaluator previously fed independent WAV clips to one openWakeWord detector without clearing its temporal state between clips. The source now calls `reset()` and feeds 16 silent 1,280-sample frames before each independent WAV. A live microphone remains one continuous stream; this reset is limited to the offline evaluator. The acceptance threshold remains 0.65.

For the v4/Yuna six-clip report, all **6/6** input WAV SHA-256 values stayed identical. Its recorded 3/3 positive accepts and 1/3 negative false accepts became **2/3** and **0/3** on those same WAVs after the reset. The former negative score of 0.993801 for the ordinary greeting became 0.264813. This paired observation supports detector-state carryover as a cause of the earlier false accept; it also exposes a missed positive.

For the v5 held-out report, the original **12/12** WAVs were still available locally. Their SHA-256 values matched the report at commit `a778b10`. The corrected evaluator read those exact files without re-synthesizing them:

| Candidate | Prior score on the same WAVs | Score with per-WAV reset |
| --- | ---: | ---: |
| v5 positive accepts | 2/2 | 2/2 |
| v5 negative false accepts | 2/10 | 1/10 |
| v4 positive accepts | 1/2 | 1/2 |
| v4 negative false accepts | 4/10 | 1/10 |

The refreshed [v5 JSON report](alden-wake-v5-heldout-eval.json) instead used newly synthesized WAVs: **0/12** SHA-256 values matched the prior report. On that different corpus, v5 scored **1/2** positive accepts and **0/10** false accepts, while v4 scored **0/2** and **1/10**. Differences between the two corpus runs cannot be attributed solely to detector reset. The preserved prior WAVs are local audit material and are not included in the repository; the report at `a778b10` retains their hashes, but this paired run is not independently reproducible from checked-in audio alone.

The synthetic release condition still fails: v4 misses one of three Yuna positives, and v5 either falsely accepts one negative on the prior corpus or misses one positive on the regenerated corpus. Human-speaker and microphone/room trials remain absent. `RELEASED_WAKE_MODEL=None` continues to block live voice startup. The focused wake/evidence/runtime suite passed **49/49** under the existing Python 3.12 voice environment. Under a working Python 3.11.9 interpreter, the same **49** tests ran with **11 skipped** because NumPy and audio dependencies were not installed there; that run does not establish full pinned-runtime reproducibility.
