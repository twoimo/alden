# Graph component browser verification — 2026-10-27

The user authorized a task-owned isolated Chrome for Testing context. The configured MCP rendered source `af36aa02ab80708c44fb61060a15c0a283fdabfb` over the original frozen graph observed at 2026-10-27T01:51:52.780533+00:00. Actual product `unifiedGraph`, parser and `KnowledgeDrilldown` admitted 1,984 nodes and 2,693 display relation groups with the saved five-project filter and `memory=null`. Source affinity was null; this run does not validate semantic vectors or fresh source permissions.

Chrome for Testing 156.0.8078.47 used WebGL2 through ANGLE Metal on Apple M5 Max, at a 1,200×919 CSS-pixel viewport and DPR 1. The source memory-theme ancestors were initially missing from the component wrapper. Applying the actual `.settings-shell[data-settings-page="memory"]` and `.settings-main` selectors produced the source navy palette without changing the canvas rectangle or product graph data. Initial and corrected screenshots were visually inspected and retained locally. A static reproduction with these wrapper classes passed TypeScript and Vite build; that rebuilt wrapper was not separately cold-rendered.

## Observed behavior

- Trusted pointer and keyboard activation selected the expected saved node identity. Pointer targeting used a temporary spatial marker with pointer events disabled; the event reached the actual canvas and reported `isTrusted=true`. Keyboard Tab/Enter reached the actual accessibility control and also reported `isTrusted=true`.
- Reflow preserved the selected identity and exact solver target. Expansion showed 15 nodes at two hops; back restored the selected full graph and reset cleared selection.
- At natural idle, the renderer count stayed 295→295 over one second, with no pending frame. Explicit stop held count 1,789 for one second; explicit resume advanced to 1,813. Disposal followed by start-after-disposal stayed stopped and cleared label/accessibility children.
- Scripted DOM wheel events (`isTrusted=false`) advanced label level to 3. The inspected zoom screenshot showed 21 labels, no intersecting label boxes and no viewport-clipped labels. This is scripted zoom behavior; trusted physical wheel and drag/rotation were not exercised.
- The page fetched five static assets and two worker-chunk requests successfully. The only recorded console error was the missing favicon404; no component window error or model/voice/source-service request was observed.

## Coverage and cleanup

Actual hiding was **not proven**: both page observations remained `visible`. Stable counts while idle do not establish hidden-page lifecycle behavior. Other viewport sizes, physical Retina, native windows, lid/lock, positive fresh node activity, GPU time, VRAM, FPS, p95, battery and whole-app resources remain unverified. These component results do not close native installation/TCC, the full ST01–16 scope or release acceptance.

Both task pages were closed and the pre-existing blank page was preserved. The task server ended normally at its bounded lifetime, exit0. Alden stayed OFF; no browser profile, application installation, model, service, global setting or capacity was changed. Captures with source labels stay in the existing configured MCP output root; private data was not uploaded to GitHub or another model.

Task evidence is stored outside the repository in `outputs/graph-browser-component-20261027`; the local result records the authorized capture directory, hashes, raw DOM/event receipts and cleanup. Required formal web-Astra/Gemini/reverse review remains incomplete and distinct from this authorized component run.
