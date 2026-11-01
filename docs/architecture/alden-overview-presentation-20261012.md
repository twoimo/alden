# Dense overview presentation in 0.3.80

Large knowledge overviews previously used the same wide world bounds in a
portrait and a landscape viewport. The portrait camera backed away to contain
the far horizontal points, leaving most nodes in a small central patch.

`overview-presentation.ts` maps only the displayed coordinates of overview
views above the existing 120-node physics capacity. A bounded radial gain gives
the inner cloud more space; horizontal compression matches the viewport aspect.
Circular marker area follows the horizontal compression while preserving the existing pixel-size floor. The mapping is continuous, leaves the origin fixed and stays inside the existing
ellipsoid. It adds no nodes, edges, labels or semantic assertions.

The R/S solver, its budgets and checkpoints, original and saved coordinates,
source versions and identity policy are unchanged. Sparse and local views use
exact coordinates. Screen proximity remains a presentation property, rather
than evidence of a source relation, identity equality or semantic accuracy.

Camera fitting uses the same display mapping as node meshes, edge endpoints,
labels, picking and the on-demand projected-circle audit. The existing pixel
motion stop criterion includes a conservative derivative bound. Rendering still
stops through the existing visibility and idle lifecycle.

## Scoped validation

- UI758 in72 files; type checking; native136 and strict Clippy passed locally.
- Four independent synthetic tests cover exact local/sparse coordinates,
  input preservation, finite bounded radial order, viewport occupancy and matching GPU/picker radii.
- Actual captured source and validated E5 inputs were replayed numerically.
  Compact central-quarter occupancy was95.07% before and78.08% with the display
  mapping. This replay uses a uniform tiny core radius and excludes native
  physics, GPU rasterization, input latency and semantic truth.
- Nine alternating V8 measurement pairs,200 updates each after four warmup
  pairs, measured2048-point mapping only. Median CPU time was0.00707ms for the
  exact copy and0.04055ms for the mapping. This added cost is not a speedup or
  whole-application CPU/GPU/RAM/battery measurement.
- A rejected hub-rest-length experiment failed its independent clearance test
  and did not improve the actual compact replay. Its source and raw results are
  retained outside the repository; the original solver was restored byte-for-byte.

Actual candidate/install observations and the final receipt are recorded in
`outputs/graph-hub-spacing-20261012/` in the parent task workspace. The first
native candidate inspection failed at the existing quality-query timeout after
53,139ms. Its partial graph capture is preserved; it is not a successful full
workspace inspection. Acceptance and delivery require separate terminal results.

The supplied images define visual and measurement direction. Their agent,
token, memory and latency counts are not Alden data. Global semantic quality,
remaining source duplicate candidates, outlier interpretation, physical-device
measurements, notarization and production release remain separate goal items.

The first complete candidate inspection showed compact core overlap increasing from358 to413, despite better occupancy. It was rejected before installation. Circular glyph area now follows the aspect compression, with the existing visibility floor and matching GPU/picker/audit radii. Final native results are separate below.

## Installed native result

Build source `bc78cbd8966a7931120b96990222a4e0502ee500` was installed as0.3.80.
The final candidate and installed audits each completed14 workspace states with28
PNG files. Four candidate and two installed graph PNGs were directly reviewed;
this does not establish visual review of every workspace capture.

| Window | .79→.80 installed core overlaps | Central quarter | Nearest median CSS px |
| --- | --- | --- | --- |
| Default | 324→245 | 73.39%→64.21% | 4.98→5.23 |
| Compact | 358→233 | 94.53%→77.25% | 2.15→2.94 |

All2048 centers remained visible with no invalid points, clipping or input
truncation. The displayed mapping, marker scale and fitted camera changed, so
these are combined presentation counts, not pure solver or pixel-quality gains.
Compact concentration remains substantial and is not a completed global layout.

Both installed sizes observed actual affinity/layout application, physical
solver rest and the existing strict two-second idle counter check. The final
candidate's default strict idle counter check failed by one frame, despite both
endpoint loops being stopped. That candidate limitation is retained separately.
Neither result attests physical input, Retina, primary/tray, GPU or battery use.

68 installed payload files matched the candidate; strict/deep ad-hoc signing,
independent .79 recovery, three settings, seven target declarations, two
policies, model listeners and selected collection/retrieval/voice tables were
preserved. These checks do not cover all original files, preferences or queues.
Formal signing/notarization/release/production and the full18-requirement goal
remain open. Final CI/PR/ZIP hashes and delivery gates are in the task receipt.
