# Alden 0.3.28 candidate: conversation boundaries and engine telemetry

History selection now serializes input acquisition and reads history plus counters in one SQLite snapshot. A late answer retains its original conversation ID and cannot play or enter the newly selected history. Closed/aborted pipelines cannot be resumed by choosing history. Event deduplication includes the conversation ID.

The phrase “remember this input” uses the current dialogue rather than automatically reading unrelated corpus notes. This is a narrow verified routing correction; general intent classification remains unfinished.

MLX launch arguments enable the existing loopback metrics endpoint. Real response IDs are paired with a local request ID, conversation, turn, input context version, source and observation time. Before/after engine samples remain aggregate observations; unavailable metrics use null, and they are never described as per-request or whole-app memory. Model admission covers the entire request and telemetry. Metrics permit only a body-free GET to the fixed inference service; proxies, redirects, other hosts, embedding ports and query parameters remain blocked.

## Evidence

- Focused voice/metrics/history/transport checks: 83 passed. UI baseline357, desktop Rust107 and desktop Clippy passed. Candidate app0.3.28 built before the final history/metrics edits and needs a final rebuild.
- [Alternating30-pair comparison](alden-conversation-switch-20261007.measurements.json): late playback, wrong destination storage and wrong response conversation30→0 each. Control-path p50 .7721045→.875583ms; p95 1.417708→1.257959ms. Synthetic adapters/private databases; host workloads were preserved. This is not UI, physical audio or general dialogue latency.
- [Actual request trace](alden-request-trace-20261007.json): local27B returned “확인했습니다”; backend request ID and input-context identity match. Aggregate success32→33, prompt52538→52688 and generated tokens871→873 changed. Engine cleanup still reported one running request at the final observation; completion and backend-idle are different states.
- [Existing warm27B sample](alden-27b-current-20261007.json):15/15 fact checks, zero adapter errors; TTFT p50 .179553/p95 .428445s, answer p50 .439304/p95 .602961s. Decoder observation p50 38.393847 tokens/s. This current sample is not a before/after improvement. Python client sockets were loopback11234 only.
- Installed0.3.27 owned WKWebView hide/show10×350ms: hidden render calls0, stop-observation upper bound p50 13.248375/max20.7215ms, DPR1. Native primary access was rechecked after permissions changed and succeeded; the earlier CUA timeout is historical.
- [Conversation sequence](alden-conversation-switch-20261007.html): Archify9 checks, no composition errors/warnings, four viewport containment checks, two endpoint themes, two images directly reviewed. Fixed viewer controls are English; authored labels are Korean.

## Remaining work

The mandatory legacy CLI suite has pre-existing contract failures: five pristine d136 tests reproduce three failures and two errors. The worker production source is unchanged. Unit fixtures now isolate user home and require explicit fake model transport; no failing checks have been silently skipped. The full mandatory suite, general intent routing, installed candidate, physical voice/shortcut, broader media/search tests and current expanded collection/integration goal remain unfinished.

Flash-Next iQ3.3bpw admission reached startup, but the engine rejected loading:50.60GB weights,52.68GB available, approximately57.6GB required. No memory-preflight override or model substitution was used. The selected27B remains unchanged.

Public notarization is blocked by zero Developer ID Application identities and six missing Apple signing/notary repository secrets. App builds are local ad-hoc candidates, not notarized production. No new message recipients or test sends were created.

The latest scope adds canonical shared collection skills, multi-target source lineage, logical graph integration, supported OSK synchronization, durable history and real-event3D interaction. See the append-only task STATUS.md for private evidence and remaining actions.
