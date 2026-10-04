# Alden source-grounded OSK distillation

The existing local Qwen3.8 27B generated three research-note proposals from three captured newsletter posts and primary arXiv abstracts. The verified papers were [long-horizon agent evaluation](https://arxiv.org/abs/2608.13417), [claim-level reliability assessment](https://arxiv.org/abs/2608.11994), and [MobileMem](https://arxiv.org/abs/2608.13606). Verification covered title, abstract and publication metadata, not full-paper reproduction.

The current task agent compared each proposal with the exact source text and primary abstract. All three newsletter quotations matched. Two proposals' predicted Alden benefits were changed to untested application hypotheses, and universal titles were narrowed to bounded research conclusions. The model's quote-string schema was repaired only because each proposal named one unique source row and the quoted string occurred there. No generating model approved its own output.

Ordinary SDK `create_node` calls saved three research conclusions and one explicitly untested design synthesis in the existing source Scope. The synthesis depends on the three conclusions: three `derived-from` predicates and three body Links resolve to their actual stable IDs. These are stated premises, not co-occurrence or a forced semantic cluster. No new taxonomy, Domain or Person facet was created. The full 512-row capture was preserved byte-for-byte under `_sources`, with original identity and roles, rather than fabricated agent rounds. Private messages, IDs, paths and source files are excluded from public artifacts.

The actual SDK search located the corresponding saved conclusion within the top five for each of three targeted queries. This is a limited functional readback, not independent Recall@k or nDCG evidence. All 22 native validator segments passed without skips. The existing four governance references and unprotected governance warnings remain. The finite source review is distinct from the unfinished corpus-wide semantic and organization review.

## Generation measurement

One successful nonstream local request took 28.242 seconds. The server reported 4,319 prompt tokens, 4,288 cached prompt tokens, 1,128 output tokens, 24.212 seconds of decoding and 46.589 output tokens/s. This is n=1 with a warmed prompt from an earlier rejected attempt; no cold-start, TTFT, p95, general quality or performance-improvement claim follows. The first attempt reached the voice transport's 64 KiB SSE ceiling and wrote no knowledge. The retry used a bounded nonstream response and retained the original voice guard. Existing models, defaults, settings, queues and five operational workers were preserved.

## Saved-note reader correction

The prior API returned a note body in the compatibility `facts` array while the UI consumed only summary and key facts. Version 0.3.22 gives the canonical body its own bounded field and shows it in the adjacent reader. Saved node links resolve by unique identity, including the SDK ID of imported nodes; equal display names cannot choose an identity. Explicit HTTP(S) links remain clickable, while HTML and unsupported schemes remain literal text. No remote image is loaded from a body. Actor/room focus, including lookup through an OSK ID alias, keeps room/account boundaries and excludes a cross-room canonical body.

Owned workspace audits can target one canonical note with `--focus-node osk:<id>`. They wait for the actual focus lookup to settle and require a returned body. The fixed audit selectors, private output directory and command allowlist remain; arbitrary caller scripts and production writes are not registered. This verifies an owned WKWebView, not physical primary/tray/voice/shortcut/OS-lock behavior.

Full goal work remains active: complete OSK semantic/organization review, canonical retrieval integration, whole-app measurements, physical interaction/voice, Flash-Next admission, Dot invocation, signing/notarization and final production verification.

## Volumetric graph

Version 0.3.23 deepens the global layout and uses a lit standard material on the existing low-poly instanced spheres. The same 120 slots, matrix/color buffers, geometry and single node draw remain. No texture, shadow pass, ambient rotation or additional object is introduced. Local 24-node bounds remain unchanged. Actual references drive springs and growth/retraction; user orbit gestures drive camera movement. Isolated IDs receive stable layout coordinates, without implied category or confidence tiers. Hidden/settled-loop policy remains unchanged.

On a fixed actual 104-node graph, the global z span changed from 1.678564 to 2.663115 world units. This is a geometric depth measurement, not a speed or quality ranking. Single layout timings were taken only to confirm bounded execution and are not a performance comparison. Constellation, relation-layout and camera-fit checks passed 19 tests, including retained slots/buffers, identity invariance, expired links, bounds and picking.
