# Rejected local knowledge completion observations

Previously, strict protocol rejection happened before a hop was appended to the
generation receipt. A completion containing two tool calls correctly performed
zero knowledge reads, but its persisted failure omitted the observed call count
and HTTP finish reason.

Typed local knowledge requests now collect a fresh `tool_loop.wire_observations`
list. Each decoded completion is observed before `parse_completion` accepts or
rejects it. The ordinary host loop still permits at most two completions and one
authorized knowledge read. Replay reads the original receipt without another
completion or knowledge read.

The protocol API, error codes, request headers, model/source binding, cancellation
and deadline checks are unchanged. Response observations do not select a call,
repair arguments, change termination or authorize a source read.

## Safe fields and meaning

Each observation identifies `origin=local_http_completion`, the bounded response
byte count, the host's `allow_tool` value and the outer JSON shape. Progressive
fields describe choices, strict integer index zero, the first message, tool-call
list shape/count and legacy `function_call` presence. Counts are actual list
lengths capped at64, with a separate overflow flag. Missing/unavailable structures
have no invented zero count.

`finish_reason` retains only a closed vocabulary. Missing, null, wrong-type and
unknown values are distinguished; arbitrary strings are replaced by `unknown`.
Content, arguments, prefaces, response IDs, model strings and source values are
never included in these observations. The durable sanitizer makes fresh copies,
requires valid provenance bases, permits at most two rows and discards unknown
fields or malformed optional values.

A wire finish reason describes the HTTP envelope. It does not attest the native
decoder's original finish reason, a stop match or spans removed by a serving
formatter. Successful parsing and actual source execution remain separate facts.

Non-JSON MIME, malformed/duplicate-key/nonfinite JSON, invalid UTF-8 and oversized
bodies do not produce a decoded observation. Existing transport failures remain
failures.

## The existing4 KiB receipt limit

Normal receipts retain full observations. The same limit also covers existing
query provenance, usage and attachment evidence. Budget fitting runs after
sanitization, after terminal/attachment fields, and after a late safe failure
code is added.

When needed, optional diagnostic shape/index fields are removed with
`wire_observations_compacted=true`. Finish reasons, retained call counts and
overflow flags keep their meanings. If further space is needed, older optional
observations are omitted first so the latest response has priority, with
`wire_observations_omitted_for_budget=true` when that marker fits. If no observation
fits, the list is absent; it never becomes an empty list claiming zero responses.
If even the omission marker cannot fit, it is absent too. An absent diagnostic
field means unobserved or omitted, not zero or success.

This removes only newly added optional diagnostics. Existing request/response
bindings, usage, source/query hashes, attachment evidence, parsed hops, model
output and protocol rejection are preserved. Primary evidence that already
exceeds the limit still fails validation.

## Verification boundary

Regressions use the real typed-input, protocol, generation sanitizer and receipt
storage paths with generated collection data and mocked HTTP. They cover
duplicate-call rejection before a source read, incomplete/truncated/malformed
replies, second-hop rejection, positive tool/final/no-call cases, exact response
byte counts, persisted readback and replay, privacy, fresh copies and capacity
boundaries. The new module is included in the normal CI Python module list.

These tests establish product plumbing and failure evidence. Actual local model
choice, serving capability, original-data execution, installed UI and release
remain separate validation requirements.
