# Keep advertised max effort through input completion

Baseline: `7d113006ceb0b23532eddf645b34f6b1e52cd2b6`. This is an uninstalled
compatibility correction within the full Alden goal, not a change to the user's
saved model, effort, automatic-selection policy or release gates.

The routing vocabulary included `max`, but `alden_chat_metrics` maintained a
second list without it. A selected route and completed response could therefore
reach the final input commit and fail with `generation_metadata_invalid`.
The ledger now snapshots the routing module's vocabulary. The catalog still
checks each model's advertised capabilities; this does not permit an unsupported
effort, choose another model, or attest the backend's actual reasoning level.

The corrected regression drives real route save/readback, input reservation,
adapter request construction, response parsing, generation sanitation, durable
completion and same-ID replay. Its catalog and JSON response are explicit
fixtures. It verifies requested model/max effort, the existing 2,048-token output
budget, one transport call, one saved answer, unchanged saved configuration and
identical replay. Live HTTP is forbidden. A separate check rejects unknown,
mistyped and malformed efforts; the route check still refuses `max` when the
fixture model advertises only low/high. Response effort is never invented from
the selected/requested value.

The immutable baseline metrics module first rejects the corrected input journey
with `generation_metadata_invalid`; its pure sanitation case also fails. Initial
test harness errors (an unpatched adapter catalog binding, then a missing
assistant response role) are retained separately and are not defect reproducers.
Production source was not temporarily reverted to run the baseline.

Validation: 180 affected checks in 10 fresh pinned Python processes and 87 UI
checks in three relevant files pass; TypeScript/Vite build passes. No UI source
changed. Evidence is under `outputs/max-effort-20261024/`.

These checks establish the candidate's configuration/transport/ledger contract,
not current provider support, live `chatgpt-web/gpt-6-astra` inference, model
availability, native GUI acceptance or installation. No provider generation,
model load or production preference write is part of this verification.
The earlier actual HTTP429, SDK qualification, current-source external audit and
installation gates remain separate. The full 18/ST01–16/Cursor8 goal stays open.
