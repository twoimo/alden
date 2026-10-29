# Installed media and followup verification

The installed Alden 0.3.114 runtime, source `ea331e5ecfb8fccaf93acf0d5d2f9ed8887e13aa`, completed four real `process_input` turns using the saved Gemini 3.8 Flash/high/manual selection. The same installed parsers, routing adapter, completion checks and typed-history store were used. Only the ledger and attachments were isolated in a private task directory; the existing operational emergency gate remained authoritative. No generation output or document parser was mocked, and no new local LLM was started.

The first turn carried the user's graph image plus an owned fictional text memo. The second had no new attachment and restored the same image/file hashes from the completed preceding turn in that conversation. The third opened a new conversation and fetched the official [SQLite WAL page](https://www.sqlite.org/wal.html). Its persisted link evidence reported one parsed snapshot; the previous conversation's image/document was absent. The fourth opened another conversation and submitted four owned minimal PDF, Word, Excel and PowerPoint fixtures together. All four fixture codes and byte hashes matched the response/provenance, including the explicit distinction between a stored formula cache and evaluating a formula.

| Installed turn | Full backend elapsed, seconds | Observed scope |
|---|---:|---|
| Image and text memo | 6.360 | Received image bytes, text parser and actual vision response |
| Same-conversation followup | 4.153 | Prior image/document origin and hashes preserved |
| Public link in a new conversation | 9.767 | Live fetch, parsing, excerpt qualification and actual response |
| Four document formats | 8.984 | `macos-pdfkit`, `docx-xml`, `xlsx-xml`, `pptx-xml` and actual response |

These are one uncontrolled observation per turn, including parsing/selection/storage and generation. They are not UI latency, p50/p95, pricing, billing, a before/after performance comparison or a general parser benchmark. The four completed request IDs were replayed without a new application generation or duplicate user/assistant ledger rows. Response model and high effort, turn/context binding and saved generation metadata were checked separately.

## Accuracy remains a separate gate

The literal name expectation derived from the user's wording disagreed by one character with both image replies. The initial semantic checks remain failed. A read-only diagnostic with installed Apple Vision, Korean/English recognition and language correction disabled produced the model's spelling with a low engine confidence field. Agreement between two automatic readers does not settle the original text or the person's identity. No expected value was changed to make the check pass; no identity or graph source was corrected or merged.

The other two visible labels and fictional memo code/date matched the independent input expectations. All four document codes matched their owned source fixtures. This does not establish arbitrary image OCR, full Office rendering, scanned-PDF interpretation, embedded media, formula evaluation or independent real-source semantic gold. The diagnostic OCR helper is task evidence, not a newly deployed product feature. Its API is documented by [Apple Vision](https://developer.apple.com/documentation/vision/vnrecognizetextrequest); the current official Markdown was retrieved after the web reader could not consume its content type.

## Preservation and remaining integration

Task evidence is in `outputs/input-context-20261014/`. Selected operational settings, seven collection targets, policies, existing model listeners and collection/retrieval/history table hashes are compared around the actual requests, together with all installed files/modes and strict signing. No external message or original-source modification was part of these probes.

The native CUA observation timed out with `-10005`; LaunchAgent readback still showed the existing primary process running. No restart or physical-input success was inferred. This backend verification does not replace physical attachment selection/submission, voice input, concurrent multi-host input, cancellation/retraction during live generation or representative whole-app resource measurement. The full18 goal remains active. This is documentation and verification of the existing installed runtime, not a new runtime release.
