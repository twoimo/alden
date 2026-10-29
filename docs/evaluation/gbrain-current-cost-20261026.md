# GBrain current-source cost diagnosis

This records bounded local diagnostics against product `4e98076a7a14316bcaa5a1e46474b6673b0752e9`. No production code, runtime pin, reader grant or currentness rule changed. The complete Alden goal and existing external, installation and release holds remain active. Alden stayed off and the original collector continued running.

## Cost observation

One diagnostic run exported the declared five-project source with query-only SQL and verified a disposable copy of the existing closed, unpublished engine. The source export and frozen engine have different epochs. Timers include guards and instrumentation; nested timers overlap. These phases must not be added into a production end-to-end latency or treated as a performance comparison.

| Phase | Seconds | Scope |
| --- | ---: | --- |
| Canonical source export | 6.718612 | 43 targets, 26,651 nodes, 48,355 relation groups |
| Closed-cache admission | 9.350271 | Existing integrity, ownership and source authority checks |
| Closed-engine copy | 2.287033 | Owned disposable engine |
| Complete native readback | 84.507839 | 26,694 pages and 75,006 outgoing links |
| Post-authority check and cleanup | 8.282120 | Existing checkpoint retained |

The readback issued 53,715 RPCs: 267 page listings, 26,694 page reads, 26,752 outgoing-link reads and two handshake/catalog requests. It retained complete page, opaque revision, metadata and outgoing provenance checks, including the high-degree paged outgoing set. The child was reaped and its copy removed. The existing pending checkpoint remained byte-identical, unpublished, inactive and not ready.

The first SQL connection telemetry was empty: the diagnostic compared `CollectionStore.root` (the collection directory) with the state root. That instrumentation error did not invalidate the completed export/native proof and is retained in the original result. A source-only follow-up corrected the predicate to the exact canonical database path; it did not repeat native validation. Twelve query-only connection contexts were measured, with a longest outer context of 5.637708 seconds and export wall time of 6.315712 seconds. A connection context includes non-SQL work. Neither value measures SQL-lock or collector-blocking duration.

## Individual-frame overlap experiment

Static audits read the pinned GBrain source and the exact npm `@modelcontextprotocol/sdk@1.32.1` archive, whose SHA-512 matches the upstream lock file. The archive was not installed or executed. The SDK starts independent asynchronous request handlers and copies request IDs into responses. Its stdio writer waits for `drain` after a false write return, but does not impose a global outstanding-request or output-byte limit. GBrain replaces the transport's `onclose` callback after SDK connection; SDK map/abort cleanup therefore cannot be assumed on that path. These are source findings, not proof of native-bundle scheduling or shutdown behavior. Existing caller deadlines, process reaping and inherited leases remain necessary.

A separate read-only engine copy compared windows of one and two **individual newline JSON objects**, not JSON-RPC batch arrays. Both used the unchanged page/link decoders and canonical index checks for the same 128 pages and 129 outgoing links. Handshake and final catalog checks were serial; no traversal, cursor chain or mutation overlapped. A window contained at most 500 request bytes and 7,028 response bytes, below its explicit request/response bounds.

| Order | Window | Seconds | Verified content |
| --- | ---: | ---: | --- |
| 1 | 1 | 0.922478 | Same complete selected pages/links |
| 2 | 2 | 0.352553 | Same complete selected pages/links |
| 3 | 2 | 0.396000 | Same complete selected pages/links |
| 4 | 1 | 0.374115 | Same complete selected pages/links |

All four decoded-content hashes matched. Replies arrived out of request order in 250 of the 256 two-request windows, confirming that FIFO result attribution would be incorrect. The warmed window-one observation is comparable in scale to window two; this small experiment does not establish a latency improvement. No production multiplexing was adopted, and no large-window, malformed-frame, cancellation or backpressure acceptance is claimed.

## Optional duplicate Markdown experiment

GBrain `get_page` can return its ordinary body/frontmatter fields together with an additional serialized Markdown `content` field. The existing Alden page verifier checks the ordinary fields. A second read-only copy compared `include_content` true/false on the same 512 pages, after a full warmup for each shape. The existing page verifier was used in all six rounds; every field except the optional `content` and its `chars` accounting was byte-equivalent after canonical JSON encoding.

The two measured true observations took 0.699275/0.638707 seconds; false took 0.713692/0.726147 seconds. Re-encoded decoded JSON was 1,153,812 bytes with the duplicate field and 847,899 without. These are decoded payload sizes, not exact wire bytes, tokens or cost savings. No latency improvement was established, so production response shape remains unchanged.

## Acceptance limits

All three diagnostic engine children were reaped and their copies removed. The original pending pointer/owner and sole retained generation survived; no active/ready grant was created. Original operational SQL writes, collector pauses, GUI launches, installation, provider calls and global configuration changes were zero. Native Astra supplied additional bounded source audits and was closed afterward; it does not substitute for the held independent web-Astra/ChatGPT–Gemini–reverse reviews.

These results diagnose costs and reject unsupported improvement claims. They do not establish latest-source publication, semantic truth, installed UI/TCC/native-account behavior, external audit or release acceptance. The last actual source catch-up remains an unpublished as-of checkpoint. The next performance candidate needs a measured reduction while preserving complete proof and strict source-current fences.

Machine-readable scope: [gbrain-current-cost-20261026.json](gbrain-current-cost-20261026.json). Detailed private evidence remains local. No source bodies, raw cursors, credentials or private engine data are included here.
