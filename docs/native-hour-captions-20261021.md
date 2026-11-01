# Native hour-format captions — Alden 0.3.212

Fresh source acquisition exposed a parser incompatibility on a video exceeding
one hour. Aside's actual timestamped response switches from MM:SS to HH:MM:SS,
including mixed cues such as `[59:55 - 01:00:03]`. The existing two-component
validation rejected917 of the retained response's1,890 cues.

The adapter now validates both forms and compares mixed intervals correctly.
Its decimal-string key avoids unbounded integer conversion, including hour
components longer than Python's integer-string limit. Out-of-range minutes or
seconds, reversed intervals and four-component clocks still fail. Overlaps and
rounded zero-length intervals retain their previous behavior. Source bytes,
caption text, clock strings, IDs, source roles and capture/version authority
remain unchanged; this does not verify caption accuracy or video visuals.

Three new valid-clock cases are rejected by source/actual installed211 and pass
with the correction. Existing invalid-clock tests also verify that failures
preserve the prior checkpoint. The affected adapter/acquisition/scheduler/input/
realtime suite passes91 tests. An initial suite command named a nonexistent
test module; the import error and corrected run are retained privately.

Actual staged/installed parser tests and the retained complete1,890-cue response
are separate artifact gates. A controlled cooperative reload of the existing
collection service is needed to adopt a changed imported parser; queued work,
control state, target permissions and settings must be preserved and read back.
The ordinary app installer does not itself establish collector code adoption.

Private evidence is under `outputs/hour-caption212-20261021/` and
`outputs/source-acquisition-batch211-20261021/`. Data acquisition, native policy
changes, source/capture/index verification, signatures, PR/CI and later discovery
integration are recorded independently. The full objective, independent semantic
gold, external review, physical/model/resource and normal release gates remain
open.
