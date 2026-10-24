# Keep source popups and history navigation bound to the same record

The controller checked version/target before emitting read activity but still
returned a mismatched details payload to other consumers. The popup checked only
node ID/version, ignored failed/discarded envelopes, and could fall back to a
cached source description. A frontend-only 12,000-character clip also lacked an
excerpt notice when a payload incorrectly said it was untruncated. The normal
backend already clips at that limit with a truthful flag; the oversized fixture
does not demonstrate a backend clipping failure or production disclosure.

One exact-reference predicate now checks nonempty source version/target and node
ID. The controller rejects a successful source response whose basis/reference
does not match before title/body/fact consumers. The popup independently checks
the reference and envelope and clears source body/facts/evidence and cached body
description on failure. Collection nodes cannot silently downgrade into note
responses; ordinary saved notes and graph-bound review annotations retain their
separate behavior. Identity matching does not establish authorization: the
canonical reader still checks current project permissions and source bytes.

Each focus request also has its own generation. A→B→A cannot accept the first A's
late result or emit its read activity just because ID/version/target repeat.
Stopped controllers start no focus read. Existing query/hide/close fences remain.

INGEST and TAIL-F rows can open a source only when their document/version/target
exactly match a node in the current display. The action uses the existing graph
selection and canonical detail reader. View updates remove outdated actions,
and clicking rechecks the reference. Unresolved, older-version or out-of-view
history stays visible without pretending it opens the same retained record.
This does not implement navigation to every historical version or enlarge the
existing recent 80-row query window.

The popup discloses both backend-reported excerpts and its own clipping. It does
not add full archived-text access. Numeric source facts, retained original text,
explicit tail disclosures and escaped note/source links remain covered. Older
positive source fixtures now include the target IDs supplied by the real API;
saved-note fixtures use nodes without collection source references. A failed
source read does not erase independently labeled graph review decisions.

Read-only installed-reader verification also rejected an outdated snapshot's
version. That denial is preserved as an observation; its raw response bytes were
not retained. A new bounded snapshot cycle returned 1,984 nodes, 80 history rows
and 66 exact current matches. Its selected detail reference matched the canonical
reader. Real frontend DOM handlers over HappyDOM opened that same reference,
displayed its 9,052-character source body and receipt, hid a different-target
response, and left all frozen inputs unchanged. This combines an actual reader
boundary with a synthetic DOM target; it is not full native/physical interaction.

The original Pro thesis F-ST03/F-ST04 supplied historical counterexamples. Local
reference, history-action and focus-lifecycle failure baselines and regression
checks are retained privately under `outputs/source-popup218-20261023/`.
Actual installed-reader snapshots and native UI/build/signature/config/CI
receipts have separate scope and must not be conflated with synthetic DOM tests.
The original external thesis remains source `60003be`; Gemini and reverse review
are incomplete, and this later source delta requires applicable review.

Whole-graph permission-withdrawal/cache retirement, full retained-version access,
broader history navigation, independent semantic quality, messenger lifecycle
and live ingestion, physical interaction, representative resource measurements
and normal production release remain open goal gates.
