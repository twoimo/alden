# Alden graph background and rendering — 2026-10-09

Dense overviews previously used lit spheres for 2,048 tiny nodes, 16 ribbon segments per straight link, thousands of invisible pick meshes in the rendered scene, and labels for every displayed node. Overlapping lines produced bright clumps.

The dense batch now uses two-triangle camera-facing glow glyphs. Small views keep their existing spheres and detail glyphs. Actual source memberships determine finite, dim Gaussian envelopes; they create no nodes, semantic edges, activity or stored clusters. Uniform-volume anchor radii reduce central crowding. Quiet links brighten only when an actual endpoint is selected or hovered. The same stable IDs, scopes, source evidence, graph bounds and keyboard-accessible node list remain.

Dense straight ribbons use one segment. CPU pick meshes are detached from the draw scene and explicitly disposed. Overview labels cover at most eight source representatives plus focus and hover. Pointer moves coalesce to one pending animation frame, canceled when hidden, rebuilt or disposed. Settled link buffers skip recomputation on camera-only frames, and unchanged instance matrices skip GPU uploads. History restores retain their saved pose across note-pane ResizeObserver callbacks; actual window resizing and explicit camera reset still refit.

The deterministic synthetic CPU benchmark uses 2,048 nodes and 4,096 links, 100 warmup iterations and 1,000 measured updates per case, with five alternating sequential before/after pairs. It measures buffer updates in Node, excluding WebGL, DOM, picking, GPU execution, physical input latency and overall FPS. Geometry counts decrease from 344,064 to 4,096 star triangles and from 131,072 to 8,192 link triangles at those bounds. These are geometry budgets, not measured GPU speedups.

The final post-build five-pair median stationary CPU p50/p95 was 0.147/0.303 ms before and 0.0265/0.0613 ms after. Changing-position p50/p95 was 0.153/0.315 ms before and 0.153/0.309 ms after; this does not demonstrate a meaningful moving-frame speedup. Native read-only audits ran alongside that measurement. Earlier build-time samples and the rejected candidate remain in private evidence.

Validation: 396 frontend tests, 108 Rust tests and strict Clippy; final candidate and installed native audits each returned 20 captures with no error. Default and compact graphs, note expansion/back, saved camera targets and hidden/resumed rendering passed. Installed 0.3.44 matches 52 candidate files and strict/deep ad-hoc signature verification; model, enrollment and abort hashes are unchanged. The primary mouse/gesture check remains unverified because the computer-use service timed out.

The first CPU candidate regressed and was retained in private evidence rather than adopted. Final measurements and native candidate/installed readbacks live under the private task outputs/graph-performance-20261009 directory. UI tests, native authoritative read-only audits, installation parity, and physical primary-window checks are separate evidence classes. Public docs contain no user graph screenshots or source bodies.

Ad-hoc local installation does not complete Developer ID signing, notarization, production release, broad long-running GPU/memory/battery testing or the other active goal requirements.
