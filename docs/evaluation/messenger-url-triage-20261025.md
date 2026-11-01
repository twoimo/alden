# Case-insensitive HTTP scheme triage

The v3 messenger policy treated long uppercase `HTTPS://` links as substantive
text. They could become graph candidates even though the linked content had
not been acquired. Policy v4 recognizes ASCII case variants of HTTP(S) in both
standalone-link classification and the temporary text-without-links view.
The canonical NFC body, whitespace, hash and source identities are unchanged.

The scheme uses scoped ASCII case folding. Global Unicode case folding would
accept the long-s confusable in `httpſ`; global ASCII mode would make a URL
consume Unicode whitespace and useful adjacent Korean text. The Unicode
whitespace behavior of the URL suffix is retained. This follows the scheme
case rule in [RFC 3986 section 3.1](https://www.rfc-editor.org/rfc/rfc3986.html#section-3.1).

Verification: 226 messenger tests pass, including 23 policy tests. New checks
cover six ASCII scheme forms, five whitespace separators, confusable schemes,
unchanged body/hash, old prepared-page rejection before publication, and an
isolated two-room v3-to-v4 rebuild. The rebuild withdraws two link-only heads
from the candidate projection, preserves original pages/version bodies and
the original root, and retains the substantive document. This is a controlled
fixture, not a general v3 emulator or production migration.

Five relevant tests also pass against a private current-source copy and reject
three broken variants: global Unicode case folding, global ASCII whitespace,
and keeping the old policy identifier. Baseline v3 failed ten subtest cases.
The initial prepared-page test harness had an incorrect call signature; the
corrected harness exercises the existing publication guard without changing it.

Evidence is retained in the task's `outputs/source-quality-live-20261025/`.
Kakao passive health still reports `manifest_missing_or_invalid`; Telegram
still reports `telegram_app_changed_refresh_decoder_contract`. The production
registry has 43 targets and no messenger targets. Official SDK qualification
staging stopped at an exact-byte mismatch for a GitHub GraphQL text blob;
shared SDK files/manifests and source DBs were untouched. No live messenger
collection, production migration, semantic-truth validation or current-source
external Pro/Gemini/reverse audit is established by these tests.
