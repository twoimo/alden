# Room observation expiry — Alden 0.3.210

An unchanged observation file could retain an expired observed room name in the
materialized knowledge graph. The room-display reader already rejects observations
older than 24 hours or more than 60 seconds in the future, but synchronization's
unchanged-source shortcut did not include that time-dependent state.

The source signature now includes the reader's existing freshness predicate for
the envelope and individual rows, including inherited timestamps. It still binds
the raw file and derivation code. The next ordinary synchronization reconsiders a
name when its freshness changes, without renewing its timestamp, changing the
room's identity, or obtaining a new name by matching text. This is a freshness
correction; it does not certify an author's identity or the truth of content.

Two new regression cases fail against source/installed 0.3.209 and pass against
the corrected source. They cover inclusive expiry/future boundaries, unchanged
source bytes, expiry-driven synchronization, stable node IDs and subsequent no-op
synchronization. Tests use an owned corpus/observation fixture and the actual
bundled OSK engine. Collection is mocked; no live source, AX action, inference or
message sending is exercised. The affected source suite passes 108 tests with
the scripts directory on the import path. An initial suite command omitted that
path and had one import error; the original failure is retained privately.

The independent desktop baseline contains two approximately 30-second sampling
windows of runtime 0.3.209 and its separate collector. Desktop observed CPU means
were 0.0076% and 4.6882% of one core; the second window included a transient Python
child. These are observations under different natural background activity, not
a controlled before/after optimization comparison. Summed footprint/RSS can
double-count shared pages; sampled processes omit activity between samples.
Window/minimization state was not verified. Power profiling required superuser
access, so GPU, battery, physical-input latency and representative whole-app
performance gates remain open. The historical coalition SPI used only by the
private measurement harness is not a production dependency or public ABI promise.

Private evidence: `outputs/osk-observation-expiry210-20261021/` and
`outputs/app-runtime-baseline-20261021/`. Staged/installed artifact checks, static
configuration preservation, signatures, PR delivery and CI must be read from their
actual receipts. They are separate from source tests and the incomplete external
three-stage architecture review, physical interaction and normal release gates.
