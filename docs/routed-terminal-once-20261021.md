# First confirmed routed completion owns the answer

The routed LLM consumer continued reading SSE frames after a validated `response.completed`. A later delta appended to the final answer, a second completion replaced its text and backend evidence, and a late failure could invalidate the completed result. A provider that kept the connection open also delayed an already complete answer until a timeout or EOF.

The consumer now ends the read loop immediately after the first completion passes the existing model/status/payload validation. Its context manager closes the owned HTTP response/socket. The existing cancellation check after closing still prevents publication if the turn was cancelled. Incomplete, failed, mismatched-model and unconfirmed-EOF responses before completion retain their existing failure behavior. Input provenance, output budgets, model choice, reasoning and provider-health policy are unchanged.

## Reproduction and affected behavior

- New black-box regressions reproduce an appended late delta, a replacement completion, a later wrong-model completion and a late failure. They assert that the first text, backend ID, model observation and usage remain bound together.
- A fake stream that stays open after completion detects unnecessary EOF waiting. Another test cancels while closing the completed stream and verifies that no success is recorded or answer published.
- An owned ephemeral loopback server sends a valid completion while keeping its end open. The actual `CancellableLocalResponse` reads the HTTP/SSE bytes and closes its socket without server EOF. Only the connection factory is redirected to the owned test listener. The real router, provider, local models and user data are never contacted.
- The installed 0.3.200 module reproduced the failures under the same controlled tests: 25 tests, two assertion failures and four errors. Its source SHA-256 was `ed6fb8e2ae3cdde2a15472b577b86d78b1a0cccd0cfd9c0598e98b91ef8dd461`; its bytes and strict/deep signature remained unchanged. This is installed-module reproduction, not a live-provider incident claim.
- The changed source passed 87 affected tests across routed transport, endpoint restrictions, cancellation, voice turns/model selection, chat generation storage and metrics. No test expectation was weakened. Source and installed-consumer results remain separate in the delivery manifest.

## Original Cursor work and remaining gates

The previously recovered exact Cursor conversation was rechecked through its transcript and exact read-only saved composer row. Its 1,225-line transcript SHA-256 still matches the recovery receipt; its saved generation matches the requested identifier. The original checkout remains on its own revision with 147 dirty entries preserved, and its task document is unchanged. No Cursor agent, queue, model or session was restarted.

The original plan's W018 requires completed output to remain immutable under completed→delta, duplicate terminal and cancellation races. This correction continues that requirement in the actual product consumer, rather than treating the isolated reference package as product integration. It is partial evidence for T25/G2 only. Actual local model/tokenizer admission, durable turn and ownership boundaries, the other seven Cursor cross-check gates and the full Alden objective remain open.

Evidence is private and untracked under `outputs/terminal-once-20261021/`: failing baseline logs, affected tests, original-session readback, the installed-module harness and build/install/configuration/CI receipts. The target delivery is 0.3.201. Fixed-signature installation, unchanged settings, installed 25-test replay and final CI are separate gates recorded in the manifest and task-root progress files. There is no live provider inference, live message send, worker activation or foreground native QA in this change.
