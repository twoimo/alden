# Alden 0.3.83: dense overview viewport fit

Compact graph views used the same 96 CSS-pixel total horizontal fit margin as
local/detail views. That consumed 21.9% of the 439-pixel viewport and pushed the
camera back. The dense overview now uses 32 pixels horizontally while retaining
the 40-pixel vertical margin, minimum distance and every point's enclosing
radius/depth. No quantile cut, outlier exclusion or position rewrite is used.
Sparse/local fitting keeps the original policy.

Dense glyphs retain the existing degree variation and visible floor, with a
1.8 CSS-pixel radius ceiling at the camera target depth. This is not a universal
screen-radius bound for every depth. GPU matrices and sphere picking consume
the same bounded radius; local/detail views reset the ceiling to infinity.
Existing radial presentation, source/solver coordinates, R/S, communities,
original relationships, IDs, filters, pins and generation/idle controls remain.

The installed 0.3.82 read-only CLI captured the current 1,984 collection inputs,
memory graph and policy-v4 bound affinity. The deterministic 2,048-node replay
holds identical anchors and source inputs while comparing the camera/glyph
policies. Compact center-quarter occupancy changed 78.22% to 62.01%, occupied
cells121 to161 and nearest median2.85 to3.39 CSS pixels; core overlaps238 to235
are a limited change. Default occupancy stayed63.53% while core overlaps240 to187
changed through the glyph ceiling. Every center remains visible; invalid and
clipped centers are zero. This is numerical front-facing replay, not native
physics, GPU pixel, frame-time, whole-app resource or semantic-gold proof.

An old-source regression failed because dense fit retained the wider margin.
Tests cover enclosing glyph corners/depth, portrait/landscape sizes, unchanged
legacy fit, CSS-sized ceilings, the visible floor, resetting the ceiling and
matching GPU/picker radii. Existing labels, async fit/orbit/saved-pose tests remain.

The requested Web GPT-6 tool rejected max and reported low/medium/high/xhigh as
supported efforts. The same model's highest supported xhigh review completed,
with actual turn metadata and closure retained. It reviewed provided text only,
not files or runtime. Its bounds, depth, outlier and interaction concerns
remain separate from physical device/Retina validation.

Source UI779, native136, typechecking and read-only render-contract Python33
passed. Build, native rendering, installation, preservation, CI/PR/package and
final screenshots are distinct delivery gates. Evidence is in the task's
outputs/graph-viewport-fit-20261012/. All18 goal requirements and independent
semantic/outlier, full-source, physical/host/model and resource/release gates
remain active.
