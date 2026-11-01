# Alden 0.3.152: readable labels across zoom levels

Installed runtime source: `21246e726c7eb4502d3f37fc75e24d870577f6b6`. The previous graph used fixed density steps and could relocate a colliding label up to three rows away. Dense saved topics appeared as a detached central list; magnification changed fonts abruptly. This release keeps labels near their projected nodes, culls crowded passive names, and reveals viewport-local saved titles as the user zooms in.

Topic text follows magnification continuously in bounded quarter-pixel steps, from13 to16px. Titles stay12–13px. Content levels retain five-percent hysteresis; the budget also grows with magnification and viewport area, up to24 total ordinary/active label spans. Short subjects get different screen spacing from long titles, and equally ranked retained names have a small preference during pan. Actual measured rectangles still decide final placement and overlay avoidance. Focus/hover keep priority. An empty culled set no longer restores the whole overview.

The navy field, graph records, edges, identities, evidence policy, search and keyboard targets remain intact. Only saved labels are used; this does not infer a new subject, merge records or establish semantic truth. Legacy unspecified content evidence remains eligible under the existing policy.

The direction follows [Sigma's importance/zoom/density visibility](https://www.sigmajs.org/how-to/labels/styles-positioning/), [Ogma's zoom-dependent text](https://doc.linkurious.com/ogma/latest/examples/edge-text-zoom.html) and [Cytoscape's readable minimum zoomed font size](https://js.cytoscape.org/#style/labels). Alden keeps its own renderer; these libraries were not added.

## Installed observations

Actual current2048-node read, owned native WKWebView with synthetic wheel input:

| Viewport | Distant | Overview | Context | Detail |
|---|---:|---:|---:|---:|
|1200×760|2|4|15|21|
|640×680|2|4|10|10|

These are observed visible counts for this graph, not fixed product promises. The old151 view showed4/8/15/23 and4/8/12/13 respectively. Both old and new captured rectangles had zero overlap/clipping/unpositioned labels; the improvement is presentation hierarchy, bounded displacement and controlled density, not a claim that the previous rectangle-overlap count was nonzero. Node geometry and saved graph data were not changed.

All eight final candidate and eight installed views were inspected directly. The first152 candidate hid too many short subjects (two overview names); its original captures are retained. Width-aware spacing corrected this to four overview subjects without returning to a displaced list. Detailed views reveal saved names such as 필기·인적성 and 면접 when their nodes and space are visible.

UI979 tests, Rust146, strict Clippy and canonical build passed. All77 installed files/modes matched the candidate; strict deep signature and the unchanged designated requirement passed. Independent151 recovery, model/enrollment/emergency settings and seven collection target/policy settings reconciled. The launchd primary process runs the installed bundle. The existing external MLX process was observed and left untouched. Default Flash/high/manual remains preserved.

The prior [151 conversation/MCP fixes](context-mcp-0.3.151.md) and [nine implementation diagrams](architecture/alden-0.3.151/README.md) are included as separately bounded evidence. This change does not require another model generation; the151 installed default request and packaged boundary results remain dated151 evidence.

Raw receipts are under `outputs/zoom-polish-20261019/`: baseline, first candidate, adjusted candidate and installed captures, geometry summaries, direct image reviews, test/build logs and preservation/signature receipts. Physical trackpad/Retina input, whole-app FPS/GPU/battery/p95 and external adversarial/reverse completion remain unverified. Public Developer ID/notary/production gates remain open.
