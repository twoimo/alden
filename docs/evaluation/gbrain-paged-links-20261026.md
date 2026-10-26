# Complete outgoing readback above the traversal cap

GBrain's reviewed `0.60.451.0` binary is pinned to source
`33d73cb54c14e2e988272764ddff59a5ead10915`. Its traversal operation stops at
5,000 rows; that bound cannot establish a complete outgoing set at or above
the cap. The previous actual five-project index contains a 9,436-link outgoing
set, including 9,434 links with one verb. Partitioning only by verb would not
solve that case. These are structural observations, not semantic truth.

The complete importer retains its original source archives, page proofs,
revision checks, legacy formats, current-source checks and publication guards.
For an outgoing set below the traversal cap it keeps the existing traversal
comparison. At or above the cap it uses the publisher's supported `get_links`
cursor pages. Every request retains the exact source, slug, `status=all` and
page limit. Opaque cursors are passed back unchanged. A repeated cursor,
inconsistent pagination metadata, missing or misplaced last-row markers,
foreign endpoint/source, malformed result, exceeded row budget or cancellation
fails the operation.

Every returned tuple must match the immutable complete index: both endpoints,
verb, exact context and importer provenance. Missing, extra or duplicate links
cannot complete verification. The index file identity is rechecked after the
stream. Every page still receives its independent page/revision/proof check.
Source advancement or permission withdrawal during paged verification prevents
publication and retires the owned generations. Bounded user graph/path queries
retain their previous limits.

The actual large matrix also exposed a fresh-rebuild revision error. A reused
stable index retained opaque revisions from the prior engine, but a new empty
engine cannot satisfy those old revision preconditions. A six-page actual
baseline returned six terminal `revision_conflict` results and committed none.
Only the fresh `_build` path now clears the copied page's revision precondition;
the copied-engine delta path keeps its expected revisions. The corrected small
actual rebuild commits all six pages and retains the same logical fingerprint.
The strict batch acknowledgment and complete revision/proof readback remain.

The publisher's link listing truncates context to 2,000 characters. Before any
engine is created, high-degree pages whose contexts exceed a conservative
2,000 UTF-16-unit budget are rejected explicitly. Small-degree pages retain
their complete traversal contexts. No proof is accepted from clipped context;
support for high-degree, longer contexts remains open. The saved actual
five-project index has maximum context length742, so this limitation does not
exclude that measured snapshot.

Reviewed publisher notices are inert metadata. Suggested fixes, grants,
embedding calls and setup commands are never executed. Unknown notices remain
errors until their actual response and pinned source contract are reviewed.
The actual large-store read exposed `backup_coverage/coaching`; the pinned
publisher's dispatch implementation identifies it as backup advice. That exact
code/kind is accepted as inert metadata across graph, page and paged-link reads;
it grants no permission to execute the accompanying command.

Verification receipts and remaining limits are recorded in the adjacent JSON.
This is a reversible candidate under the inherited external-review and worker
holds. There is no installation, GUI launch, original-source write, SDK repair,
global MCP change, account/model/permission substitution, send or release.
The GBrain and open-ontologies references retain the pins and scope separation
documented in `gbrain-scoped-paths-20261026.md`; no ontology theorem or certificate
engine runs. The full original18/follow-up/ST01–16/Cursor goal remains active.

## Reproduction

Run `gbrain-paged-links/replay.py` with pinned Python3.11 and explicit
`ALDEN_PAGED_RUNTIME` (the already reviewed GBrain runtime directory) and
`ALDEN_PAGED_OUTPUT` (a new, absent private output directory). The optional
`ALDEN_PAGED_SCRIPTS` selects the exact script directory; it defaults to this
checkout. `ALDEN_PAGED_SINGLE=9436` selects one full9,436-boundary trial for an
independent packaged-artifact check. The default matrix covers full5,000,
delta9,436 and full9,436 outgoing links. Synthetic input is created only inside
the new output directory; all owned engine generations are withdrawn at exit.
No runtime download or original collection access occurs.
