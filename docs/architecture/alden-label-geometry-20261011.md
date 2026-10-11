# Alden 0.3.65 label geometry

Changing between overview and local view changed CSS padding while retaining the previous cached width. Collision decisions could therefore use narrower boxes than the rendered labels. The renderer now invalidates dimensions on mode changes, caches measured width and height, and checks explicit inline typography changes without requesting layout on unchanged frames. Transform changes do not invalidate the measurement cache.

Placement uses each measured height, keeps the15-slot budget and refuses a label that cannot fit in the viewport. Visual suppression preserves node identities, relationships, geometry and the keyboard/detail path. Tall text receives sufficient vertical spacing. Normalization, entity resolution and R/S authority are unchanged.

The independent synthetic adapter reproduced five failures in13 tests: local mode retained104px against the adapter's current118px box; oversized width, font-size changes, tracking changes and44px-high labels also failed. These are controlled fake DOM dimensions, not native font measurements. All13 and eight previous render-label tests passed after the fix. A later native observation found28 pairs at the origin in each of two pending reset/back-overview states; twelve other observed states had zero pairs. A separate pre-placement visibility test failed before the follow-up. New labels now start hidden until the renderer places or culls them, and native observations distinguish positioned from unpositioned elements. This avoids treating pending label DOM as a completed layout.

Final UI497/TypeScript/Vite, native119 and strict Clippy passed. The first candidate's numeric observations and the failing follow-up are retained separately. Build, installation, remote CI and final pixel/rectangle observations are separate gates.

The owned native audit samples at most24 DOM element rectangles in existing graph states, counts overlaps/clipping and records typography dimensions. It emits no new label text, source IDs or bodies and does not run in the app frame loop. Element boxes do not establish font-ink overlap, physical input/Retina, actual FPS or semantic quality. The audit marks truncation rather than claiming complete coverage.

GPT-6 Web/xhigh also prepared a deterministic planted-group R/S audit under the private evidence folder. Its execution was rejected by automatic review with security status undetermined. That action was not retried through another executor. No scores exist, and no grouping accuracy or learning is claimed. The independent original-source identity/temporal/outlier/normalization and local merge delivery gates remain open.

Evidence: `outputs/label-geometry-20261011/`, including pre-fix agent findings, separate post-fix logs, prepared but unexecuted cluster audit, and later native/delivery receipts. The entire18-part goal remains active. Developer ID/notary, normal tag/release and production delivery remain separate.
