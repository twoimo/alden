# Keep provisional page coordinates out of the solved layout cache

The renderer gives newly visible real nodes cheap ID seed coordinates while
the worker solves their layout. Previously, the driver excluded those seeds
only when no checkpoint existed. After paging to unrelated identities, any
old checkpoint made the new display seeds look like previously solved
positions. The solver then averaged those coordinates into community centers.
Returning to the first page could compress its communities toward the origin.

The driver now passes previous positions only for identities present in the
last completed checkpoint whose current coordinates are settled overview
coordinates, or explicitly pinned by the user. Surviving solved
coordinates, selection pins, authority clears and cancelled-job fences remain.
The node/edge/provenance/permission data, R/S weights, solver limits, camera
rules and stored source records are unchanged.

A new warm-checkpoint regression fails against the preceding source. It then
passes with exact surviving coordinates and the new explicit pin preserved.
All 754 UI tests and 136 native tests pass, as do TypeScript and strict Clippy.

The first candidate reduced default-view overlap but did not materially fix
compact central concentration. It was not installed. The compact audit also
traverses a local expanded view: discarded overview identities receive new
display seeds on return even if their IDs remain in a completed checkpoint.
The renderer now marks fresh seeds and local-view coordinates as provisional
for overview solving. They reuse completed overview targets, and the marker
is cleared only after the current overview has rendered a settled layout.
Even an unchanged view key must restore targets when current coordinates are
provisional. A regression covers this exact cache-restoration boundary.

Constructor-bypassing render test fixtures initially lacked the new marker
set, causing 21 unrelated fixture failures. Their initialization was brought
into line with the actual constructor; product guards were not weakened.

The next native compact capture restored geometry but still used a camera fit
made from temporary display points. The existing one-time asynchronous fit
completion is now also armed for large overviews when an explicit fit sees
provisional positions. Ordinary data updates do not arm it, and orbit input
or a restored camera pose still prevents a late refit. A 25-node regression
checks the smaller final distance, visible centers and absence of repeat fits.
This changes requested camera-fit completion as well as geometry; actual
before/after poses remain part of the native comparison.

## Evidence and measurement scope

Actual read-only captures include 1,984 collection nodes and 64 displayed
memory nodes, the next 1,984-node page, and the existing validated E5 affinity.
A deterministic driver/solver replay follows relation-only → enriched →
next-page → returned relation-only → returned enriched stages. The old final
fraction inside a radius-0.5 world sphere is 17.04%, compared with the initial
9.13%. The correction reproduces the original 9.13% and original coordinates.
That is solver evidence; it does not prove camera, pixel or semantic quality.

An explicit native audit separately measures projected reference core circles.
It uses the actual inset rendering viewport and current camera/world matrices,
not the outer canvas size. Exact pair work is capped at 2,096,128 tests over
2,048 points and never runs in the ordinary frame loop. The snapshot exports
counts and numerical geometry only, without node text or identities.

The instrumented 0.3.78 baseline at source 122e3d5 has 491 overlapping core
pairs in the default view and 388 in the compact view. Center-quarter fractions
are 88.23% and 92.77%, with all 2,048 centers inside each viewport. These are
geometric core circles: glow, labels, occlusion, actual rasterization and human
semantic truth are not certified. The baseline instrumented bundle is retained
privately and was not installed over the original 0.3.78 application.

Candidate and installed measurements at both sizes remain the delivery gate.
The graph still shows a bounded slice rather than every knowledge record.
Whole-graph crowding, independent source/semantic gold, duplicate candidates
and the remaining eighteen-requirement goal gates remain separate work.

The requested Web GPT-6/xhigh metric subtask stopped before editing: its host
workspace tool was unavailable and subsequent inspection reported that the
request's security status could not be determined. That worker was closed
without retrying its denied path. The existing authorized native workspace
path was used for the pure metric helper and its five tests.
