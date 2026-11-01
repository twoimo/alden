# Preserve validated routed refusal responses

A completed assistant refusal was discarded by the routed Responses consumer. JSON and SSE refusals consequently failed as incomplete responses and marked an otherwise responding model unavailable. If SSE first emitted ordinary text, a completed refusal instead left that unconfirmed partial text as the published answer. Mixed final text and refusal content lost the refusal.

The consumer now preserves assistant `refusal` content alongside `output_text`, in terminal content order. It rejects missing, non-string or blank refusal fields. A validated final refusal therefore replaces accumulated partial text through the existing terminal-response path. Non-assistant output stays excluded. The selected model, completion status, cancellation, input provenance, response budget and first-terminal ownership checks remain in place.

This follows the official [Responses output message schema](https://developers.openai.com/api/reference/resources/responses/methods/create), which includes `ResponseOutputRefusal` in assistant message content. Intermediate refusal events are not published independently; the existing final-response validation owns publication. No first-refusal-token timing is inferred.

## Verification and delivery scope

- Six new black-box tests cover valid JSON/SSE refusals, partial replacement, five malformed values, mixed content order, non-assistant exclusion and an owned real HTTP/SSE connection. On the actual installed 0.3.213 modules they reproduced eight assertion failures and two expected incomplete-response errors. The original module SHA-256 was `5a528ae49e66f08edbdba745bc7736783784995f59fba848e1d63811723eada9`.
- Corrected source passed 107 affected tests across routed transport, cancellation, route availability, voice selection/turns, chat generation storage and metrics. The HTTP test redirects only its connection factory to an owned ephemeral loopback listener. It verifies final refusal text, route availability and owned socket closure while the server remains open. No real provider or model is contacted.
- The target delivery is 0.3.214. Signed staging, actual installed-module replay, file/configuration preservation, PR head and CI are separate gates recorded in private `outputs/routed-refusal214-20261021/`. The independent 0.3.213 recovery bundle is preserved before building.

This is a focused product-consumer fix under the active Alden goal. It does not establish live upstream refusal behavior, physical UI or voice behavior, representative performance gains, worker activation or a production release. Existing settings, queues, local models and unrelated services remain protected. The original terminal architecture thesis and Gemini/reverse research stages remain incomplete; the user's availability reply and one-review priority authorization are preserved without another permission request or a model substitution.
