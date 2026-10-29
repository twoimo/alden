# Hallmark adoption for the Alden workspace

A collection read failure must not look like an empty graph, a verified zero-room registry, or a model check that is still running. This change applies Hallmark's existing-app redesign workflow to those states while retaining Alden's controls, original sources and operational boundaries.

## Source and design decisions

Reference: [Nutlope/hallmark](https://github.com/Nutlope/hallmark), commit `7ec868225c6caa040558c321ee0fefa0e73f83a8`, package version `2.0.0`, MIT. This is a task-pinned application of its redesign, BEFORE/KEPT and verification methods. It does not install global skills, hooks, fonts, browser runtimes or dependencies.

Alden is an application screen. Its workspace, navigation and reading surfaces use the existing warm light/dark palette and local fonts. The graph retains its navy observatory material and all six existing terminal panels. Marketing-page hero, fold, photography and word-count rules do not apply. The Georgia wordmark, system UI/mono faces, 560×420 panel geometry, source identity and existing routes remain.

Palette constants are centralized in CSS. Derived graph and terminal roles are rebound where the graph scope is declared: custom-property aliases inherited from `:root` have already resolved and otherwise carry the warm shell's values into the graph. Focus styles are immediate, reduced motion disables navigation transitions, and the terminal controls are 36px high with 12px text and an inward focus outline that survives internal scrolling.

## Truthful source states

- The graph displays loading, empty, partial, stale and unavailable states separately. Only an accepted, well-formed zero-node response can establish an empty result. Partial/stale responses remain qualified. A failed update retains the existing graph and shows a compact notice over it.
- Graph summary and sync status remain visible on narrow screens. Filters, source scope, quality controls, accessible node list, navigation, pause, layout controls and note-popup targets keep their IDs and handlers.
- INGEST/TAIL-F claim loading only after a history request starts. Before a request, they say the history has not yet been checked. Existing active/epoch/error guards remain.
- Catalog failure ends the model's loading labels and withdraws its availability claim while preserving a previously displayed selection and effort. It performs no preference write or fallback model selection.
- Failed room-registry reads show an unverified count and list state. Entering an editor cannot promote the unread in-memory empty array to zero registered rooms. An unread room picker also distinguishes an unknown list from no search matches. Writes and their existing readback paths remain.
- The small panel's unavailable status and source title occupy separate vertical positions.

## Lifecycle corrections from native Astra review

The independent native `gpt-6-astra / ultra` review identified five concrete paths. Corrective checks cover scope invalidation before a delayed read (including paused scopes and discarded replies), a shared initial header/map classification for empty/partial/stale/unavailable, a rejected read after preferences disposal, model-menu health after a failed catalog refresh, and history loading after pause followed by resolve/reject and recovery. These are bounded source/synthetic tests; they are not native app or formal-state-space proof.

## Reusable preservation checker

`desktop/scripts/design-contract.mjs` uses the existing TypeScript AST to inventory root desktop HTML/templates, controls, IDs, data actions, links, selectors and event bindings without importing or executing application sources.

From `desktop`:

```sh
node scripts/design-contract.mjs --help
node scripts/design-contract.mjs before --ref <baseline-commit> --out /absolute/new/before.json --json
node scripts/design-contract.mjs kept --baseline /absolute/new/before.json --json
```

It verifies the baseline against its exact Git tree, counts repeated declarations, rejects tampered/symlinked inputs and refuses receipt overwrites. Exit 1 means a known removal/change; exit 2 means unresolved dynamic observations. Neither is success. Dynamic template holes, nested-module semantics, event delegation, visibility and runtime reachability remain outside static proof.

At the pinned Alden baseline `d928f85512677c3cb3b39a7d1e2686a749814b8b`, 497 declarations across 24 files were inventoried. Controls, IDs, forms, links and selectors are compared separately from strict callback-body fingerprints. The intentional failure/lifecycle corrections can change fingerprints for existing focus, search and pause callbacks; a changed body is not hidden from the checker or waived into a passing result. Event receivers, names, callback references and affected actions are checked through source comparison and focused behavior tests. Exact retained/changed counts and unresolved dynamic observations belong to the task's final evidence receipt.

## Verification boundary

Actual IAB rendering uses the normal local Vite route without `?demo`. Screenshots and interaction readbacks cover the renderer's real disconnected state, light/dark palettes, reduced motion, 375/640/1920px workspace widths and the 560×420 small panel. Keyboard Home/End navigation, terminal collapse/expand, independent follow control with internal scrolling, filter/info disclosures and editor entry/cancel were exercised. No source bodies or fabricated graph nodes were injected into the page.

The desktop suite covers state transitions, source fencing, detail integrity and existing controls, plus intentional preservation/terminal regressions. TypeScript and Vite builds are checked separately. Browser success does not establish native Tauri execution, actual source population, live local inference, permission persistence, GPU/performance, installed delivery or release readiness.

The unchanged upstream static linter is run with `--scope app`, not its Puppeteer renderer. Its mechanical FAIL count is zero at the final CSS checkpoint, but REFLEX/WARN observations keep exit 1 and the `.ts` application markup is outside its scanner. Thus this is not an all-gates Hallmark pass. Alden's AST checker and IAB readbacks cover different, explicitly limited evidence layers. No unapproved Chrome/Puppeteer fallback is used.

Alden remains OFF. Collection/model processes, original operational databases, pending GBrain generation, account/model/permission settings and existing external-review/release holds are preserved. Native Astra is an additional static review, not a substitute for the held web-Astra and ChatGPT/Gemini/reverse/formal stages.
