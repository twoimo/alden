# Fence late activity after a journal replacement

When stream A's activity request is still in flight and the graph accepts a new
stream B snapshot, an A response could later rewind the committed checkpoint and
animate B's visible nodes. Matching node IDs, source versions and target IDs do
not authenticate the journal generation.

`CollectionActivity.snapshot()` now advances its existing epoch when it accepts
a new baseline or replacement stream. An earlier in-flight request fails the
existing epoch fence. Ordinary same-stream refreshes still retain unread pages.

The original terminal thesis review surfaced this counterexample on revision
`60003be09176377bf62457b3a16c305011d27da6`. The affected source was unchanged in
the current checkout. A local regression reproduced the rewind from B cursor100
to A cursor15 and stale activity; the corrected consumer retains B and later
admits a real B receipt after its exact snapshot is visible. This is a local
synthetic reproduction, not an observed production incident or a completed
external three-stage audit.

Validation before packaging: 35 affected UI checks, full UI suite 1,059 checks
across 94 files, and TypeScript validation pass. The associated messenger/store
candidate has 201 affected Python checks. The normal signed 0.3.215 build,
installed byte/signature/config preservation, background native verification and
exact-head CI are separate delivery gates; their final receipts are retained in
private `outputs/stream-fence215-20261023/`.

The 0.3.215 source includes the previously tested additive store support for
messenger platform names and transactional retractions. The messenger reader,
frontier and triage adapter remain outside the product resource allowlist under
`docs/evaluation/messenger-stream-20261021/`. Installing this patch does not
activate whole-database messenger acquisition, change the existing targets or
establish source-to-view/combat performance.

The Pro thesis concerns the original 0.3.204 scope. Its report, Gemini review and
Pro reverse verification must retain that source identity and explicit missing
requirements. Current messenger changes and this correction need their own
applicable evidence/delta adjudication. No direction, semantic quality, physical
voice/Retina, measured resource improvement or normal production release is
declared complete here.
