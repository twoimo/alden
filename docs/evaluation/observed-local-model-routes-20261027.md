# Bind local generation to its observed endpoint

The previous selector guessed11235 for any model ID containing
`Flash-Next-MLX-Serve-iQ`. A controlled ready catalog for the exact IQ4.7 model
on11234 therefore selected11235, after which LocalMlxLlm rejected the endpoint.
The actual shared catalog was unloaded; the reproduced failure is a source
fixture with real catalog/selection/constructor functions, not a failed live
inference or evidence that the model is ready.

Catalog discovery now constructs `local_base_url` from the origin of its own
fixed loopback GET requests. Provider payload URLs and model-name families
never supply the endpoint. Only the existing optional single `mlx/` prefix is
an ID alias. The selected ID remains exact; IQ4.7 is never rewritten to IQ3.3.

A local route requires one observed ready endpoint and exactly one matching
row there. Ready copies on both ports or duplicate IDs on the chosen endpoint
are unavailable/nonselectable/nonautomatic, including after positive health
telemetry. An unready copy on the other endpoint does not create a second ready
route. Memory admission and the existing automatic model policy remain.

Save/selection reject missing or unsafe observed endpoints without rewriting
saved model, effort or mode. Catalog-selected adapters require an explicit
literal `http://127.0.0.1:11234/v1` or `http://127.0.0.1:11235/v1`; whitespace,
padded ports, empty credentials, empty query/fragment delimiters, trailing
slashes and outside endpoints are not observed literal routes. Legacy/default
callers retain their existing exact fixed-model port policy. The adapter still
requires that same exact ID to be loaded and ready on the selected endpoint
before a model POST. Withdrawal, port changes, response mismatch and transport
failure never trigger another model/port attempt.

The existing `local_model_not_ready` selection failure now survives the typed
failure receipt, durable generation evidence and draft-preserving UI message.
No model transport starts after a denied route.

Native Astra/ultra independently wrote18 boundary tests in a disjoint file.
Its baseline16 methods had5 passing/11 failed methods, with36 assertion and27
error records across subtests (including the absent proposed validator API).
Those are repeated boundary cases, not63 independent production bugs. The
parent added one real typed failure/ledger test:19 tests pass. They use actual
catalog/save/selection/adapter/typed functions with only HTTP and external
execution mocked; no live weights or private dataset is used.

Affected checks pass718 Python tests across32 modules, failure/error/skip0,
and103 composer/model-control tests, plus TypeScript/Vite. Supplemental
OpenCodex Argon/high reviewed the source-bound routing/HTTP/caller bundle and
substantively read the endpoint and ambiguity rules. It found no concrete
P1/P2/P3 defect; the response and canonical one-send route receipt are retained
in the task evidence. Its scope excludes full transitive configuration/source
authority and does not replace required held web-Astra/max or the original
ChatGPT→Gemini→reverse audit. No performance gain is claimed.

No saved global route, account, capacity, server, checkpoint, permission or SDK
registration changes. Alden stays OFF. Ready fixtures do not prove actual model
choice, tool execution, installed UI, resource behavior or normal release. The
full original18 requirements, follow-ups, ST01–16 and Cursor gates remain active.
