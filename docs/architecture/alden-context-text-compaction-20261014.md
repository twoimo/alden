# Retained collection text and source-proof preservation

Alden 0.3.103 reduces repeated text in the selected retrieval context while
keeping the original documents, candidates, versions, permissions and relations.
It also fixes typed chat history losing collection-specific provenance fields.

## Actual baseline and result

The installed 0.3.102 runtime returned one repeated fact in each of three
read-only career searches. The candidate removed the repeated fact from the two
Hana Savings Bank searches: three selected source IDs are represented by two
texts, with each original version, raw hash and permission proof preserved.
The repeated retained body occupied 273 characters in each search. This is text
accounting, not measured token, quota or whole-app performance savings.

The third, promotional-text query still returns separate facts. Its retained
`entityTags` and `topics` differ even though the rendered body matches. The
candidate preserves those distinctions for review; it does not infer which
annotations are correct. These are dated source claims, not currently verified
recruitment conditions or independent human relevance judgments.

## Conservative presentation contract

`retained-threads-exact-context-v1` applies only to selected, verified Threads
source records with declared author, handle, date and retained original text.
The rendered text, permission/target/project contexts, evidence classification,
and all other retained metadata must match exactly. Only source identity and
capture coordinates are excluded from the presentation match. Unicode, spacing,
media, dates, unknown annotations and the complete retained text are not folded.
Declared partial/truncated records and a truncated first context fact stay
separate. Explicit pins, latest requests and time filters disable compaction.

`facts[i]` still corresponds to `fact_provenance[i]`. The representative retains
its usual proof. A versioned `matching_sources` extension carries the separate
proofs for the identical retained texts, including each ID, original ID, version,
raw hash, source URL, observation time and current permitted memberships.
The extension asserts text presentation equality only. It never establishes
common identity, source independence, corroboration or permission to merge.
The voice prompt states this distinction and propagates context truncation to
all associated proofs.

The existing selected-source cap remains ten. No extra candidates are scanned
to fill a compacted slot; no relation sets are combined. Every selected copy
passes the existing current-version/raw-source/permission verification and
cancellation gates before it can contribute a proof. Candidate order and
stored originals remain unchanged.

## Durable chat provenance

Typed history previously filtered out `source_version`, original identity,
membership permissions and related collection evidence. Its allowed fields now
match the collection/voice proof contract. The optional matching-source extension
is validated, limited to nine other sources, rejects nested groups/repeated IDs,
and still obeys the existing seven-fact and 64 KiB history limits. Unknown fields
are excluded. Historical receipts are preserved; missing past proof is not
reconstructed or silently promoted to verified evidence.

## Verification and limits

The new regression cases first reproduced repeated context and loss of typed
source version/scope. They cover semantic/author/date/media/permission differences,
explicit selection, time filters, partial context, source-check failures,
cancellation, immutable source tables, saved history and voice truncation.
The new tests run in the normal CI workflow.

Evidence is in the task's `outputs/collection-context-duplicates-20261014/`.
The requested Web GPT-6/xhigh sidecar reviewed the provided design contract;
it did not inspect current code or execute an independent semantic gold test.
Parent actual-runtime evidence is recorded separately. The initial wrong-root
empty observation and two test-runner import/spawn failures are retained and
do not count as product failures or successful verification.

The full goal remains active: representative semantic/identity/alias/temporal
gold, conflicting annotations, physical UI/voice, multiple-host MCP, requested
RAM-admitted models, DPO, whole-app performance and normal signed/notarized
production release still require their own evidence.
