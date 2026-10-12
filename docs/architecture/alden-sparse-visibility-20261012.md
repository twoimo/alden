# Sparse graph visibility and native capture readiness

The bounded graph renderer uses the same 2,048-slot instanced batch for a small
collection and a large overview. In reference mode, its opaque center occupies
only part of each billboard. A sparse graph can therefore look almost empty
even when its current nodes have arrived.

`minimumStarRadius` uses the camera-target distance, vertical field of view and
CSS viewport height. For 1–24 displayed nodes it requests a 1.5 CSS-pixel world
radius at the target plane. For 25 or more nodes, it retains the previous 0.6
CSS-pixel radius. `ConstellationNodes` still caps the world floor at 0.03.
Depth, the shader's narrower center, and that cap affect the actual visible
size; this is not a universal minimum diameter guarantee.

The renderer preserves source IDs, relationships, degree, physical positions,
nominal radii, colors and existing GPU buffers. CPU picking uses the same
display radius. Invalid or missing positions collapse their instances.

`KnowledgeHologram.diagnostics()` records `renderedFrames` and
`renderedNodeCount` only after `renderer.render` returns successfully. Graph
receipt alone does not update either value. Hidden or disposed views do not
generate a completion certificate.

The native entity-review fixture must preserve its private synthetic SQLite
state, source-table hashes, 3→2→3 display flow, incognito scope and deadline.
Its capture readiness is verified separately from physical user input and
human entity-identity decisions. The ordinary read audit retains its command
restrictions.

Independent tests cover the 24/25 boundary, perspective projection, zoom,
resize and CSS/DPR separation, data/position preservation, picker radius,
world cap, GPU buffer reuse, invalid positions and retired IDs. Parent checks
also reject failed rendering and disposed frames as fresh paint evidence.

Actual native screenshots, installation, preservation and CI are separate
delivery gates. Raw screenshot-region pixel counts include labels and edges;
the older baseline used a different readiness condition, so those counts do
not isolate the size change or prove GPU/FPS/battery gains.
