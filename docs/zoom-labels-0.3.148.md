# Zoom-adaptive knowledge graph labels (0.3.148)

The graph previously kept eight 11px labels at every camera distance. This made the overview weakly legible and zooming in did not reveal saved document titles. The scoped outcome is readable topic hierarchy and viewport-local discovery, while keeping the navy field, source records, graph identities, selection and keyboard targets intact.

## Reading rules

| Relative camera magnification | Reading level | Label budget | Topic text |
|---|---|---|---|
| Distant | A few saved subjects | 4 | 12px |
| Fitted overview | Saved subjects | 8 | 13px |
| Context | Subjects and visible saved titles | 16 | 14px |
| Detail | More visible saved titles | 24 | 15px |

Thresholds are .72, 1.4 and 2.1 relative to this graph's fitted camera distance, with a five percent dead band. Screen area can reduce these caps. Titles stay at 11.5–12px and active labels at 14px. The actual camera minimum can limit magnification; these are presentation levels, not semantic confidence scores.

Eligible saved labels are ranked once per rebuild and projected against the viewport. Only bounded selected spans are created; collision boxes retain cached font metrics. Selected/hovered nodes reserve slots and retain priority. NFC/whitespace display duplicates, generic buckets, internal IDs, explicit unread/retracted entries remain excluded from passive titles. Legacy unspecified content evidence is still eligible: this is not a body-verified-only policy. Nodes and provenance are not merged or deleted. Position/color similarity is not evidence of identity or semantic truth.

## Official precedents

- [Sigma.js label visibility](https://www.sigmajs.org/how-to/labels/styles-positioning/): importance and zoom/density based automatic labels, with hovered labels visible.
- [Sigma.js settings](https://www.sigmajs.org/reference/settings/): rendered-size threshold and grid/density limits.
- [Cytoscape.js label styling](https://js.cytoscape.org/#style/labels): minimum zoomed font size; unreadably small labels can be suppressed.

These patterns inform Alden's own DOM/projected label implementation; neither library was added.

## Verification and delivery

Acceptance requires four projection levels, stable threshold behavior, useful title eligibility with display deduplication, unchanged source nodes/keyboard targets, bounded labels and no visible rectangle overlap/clipping at default and compact sizes. Native evidence uses actual OrbitControls with fixed synthetic wheel events inside an owned read-only WKWebView. Physical trackpad gestures, glyph-ink collision, FPS/GPU and battery are separate unmeasured gates.

Evidence directory: `outputs/graph-zoom-20261018/`. Current-source external thesis (27d02a5) is preserved separately; it predates this delta. Gemini adversarial and final reverse verification remain incomplete; this scoped implementation is not a completed project-wide cross-audit.

## Independent engine boundary corrections

Two executable regressions failed on 0.3.147 source and were corrected alongside this graph update:

- Existing pinned IDs with changed supplied coordinates now activate their affected component, matching the explicit changed-ID control. Pin output and unrelated components remain exact.
- Community modularity receives admitted semantic weight plus only eligible structural weight. A semantic edge no longer re-enables a structurally suppressed hub spoke. The physical spring weight/rest and stored source edges remain unchanged. A four-node regression exercises the 1/3 degree-ratio gate with cosine .65. Its partition is a policy regression, not a human semantic gold judgment.

Original independent audit receipts remain in `outputs/pin-boundary-20261018/` and `outputs/community-boundary-20261018/`. One exact Astra/max worker disconnected after steering; its replacement was closed before source freeze after preparing read-only baseline evidence. The final small source changes and failing/passing native-runner regressions were performed by the main session. Do not count an unfinished worker as a completed review.

F02 (multiple out-of-envelope pins using internal bounded proxies) remains open. The naive skip-clamp change crashes extreme finite inputs and was not applied. The product UI currently supplies one selected focus; this does not prove the general multi-pin engine API safe at every extreme. Current-source thesis findings about worker authority, policy-dependent label evidence and quantity symbols require separate adjudication; the completed thesis is not blanket certification of this newer source.

## Delivered verification

Runtime source `a498179246165425d8a7d07e2f33c03b05152545`, installed version 0.3.148. UI 929 tests, Rust source 145 tests and strict Clippy passed. [Exact-source CI39129847245](https://github.com/twoimo/alden/actions/runs/39129847245) passed all four jobs.

| Native viewport | Distant | Overview | Context | Detail |
|---|---:|---:|---:|---:|
| Default | 4 | 8 | 15 | 24 |
| Compact | 4 | 8 | 12 | 12 |

Candidate and installed captures both passed, sixteen PNGs directly reviewed. Every captured label rectangle had zero overlap, clipping and visible unpositioned entries. Small screens reduce capacity; collision culling can keep fewer than the cap. This is actual product rendering with synthetic wheel input, not physical gesture/FPS proof.

All 77 installed files/modes matched the candidate. Strict deep development signature, identical designated requirement, independent 0.3.147 recovery and protected settings/collection configuration reconciliation passed. Public Developer ID/notarized release remains separate. The primary app AX observer timed out after installation; it was not restarted to conceal observation failure.

An initial full-suite prototype fixture omitted the new class field and was repaired. A subsequent unrelated neuron test exceeded 5 seconds during concurrent compilation; the complete suite with two workers passed. Initial canonical input dep-info referenced a removed dist asset; a second canonical build generated and verified the receipt. Original failed diagnostic receipts remain preserved.
