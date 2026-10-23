# Preserve model failure reasons through input and reconciliation

This source candidate continues the full Alden objective's image/file input,
conversation, cancellation and failure-provenance requirements. It is not
installed. Baseline: `69904c8cd6f9b85745cb0fd60b76c63b25fdbfc8`.

## Actual installed-path observation

The installed 0.3.218 `alden_chat.process_input` received a synthetic 239-byte
PNG and 93-byte UTF-8 document in a separate private history root. It used the
actual saved Gemini 3.8 Flash/high/manual selection and existing global abort
checks. No private chats, source corpus, microphone, playback or message send
were involved. Installed modules ran with the pinned Python and `-B`.

Image receipt/native decoding/hash confirmation and document parsing completed.
Durable generation evidence retained the selected image hash and parsed text
hash. The one actual generation attempt then received HTTP429 in transport;
its saved reason was `routed_llm_quota`, but the public receipt returned only
`assistant_input_failed`. No answer was committed. The attempt took9.492s;
this failed observation is not a performance sample or successful multimodal
understanding. Follow-up generation was not attempted. HTTP429 alone does not
identify a weekly reset, remaining quota, underlying account or hidden backend.
No retry, alternative model, identity change or capacity purchase followed.

At the same observation, both fixed local generation endpoints were unavailable
and the memory-pressure level was2. The local catalog entry remained not loaded
and unselectable. No weights were loaded. The user's saved model/configuration,
abort state and installed source hashes stayed unchanged.

## Two reproduced defects and correction

1. The transport already produced a bounded reason and the durable generation
   ledger already preserved it. The input layer maintained an incomplete second
   whitelist and replaced quota/authentication/unavailability with a generic
   failure. It now uses the generation module's exact safe-code resolver after
   checking the existing input-error set. That resolver also serves generation
   metadata sanitation, so the two paths share the same membership rule.
2. Reconciliation of an already failed request returned its durable generation
   metadata without the top-level error code. A failed public receipt now recovers
   a safe code only from generation evidence whose terminal state is also failed.
   Cancelled/completed states, malformed values and code-plus-payload strings do
   not gain a derived failure code. Existing original errors remain unchanged.

The composer explains request limits, authentication, unavailability, mismatched
models and related bounded response failures while retaining the draft. It
does not automatically retry or select another model. Raw provider payloads,
paths and credentials never become error labels through this resolver.

## Verification

- Initial Python regression reproduced three HTTP429/401/503 reasons being
  replaced with `assistant_input_failed`. After that correction, the same-ID
  replay regression reproduced three missing error codes. Both failures are
  retained separately. The initial composer test also rejected three missing
  reason-specific messages.
-172 affected checks passed in10 fresh pinned Python processes, covering input,
  context, files, links, images, generation evidence, turn storage, routing and
  voice turns. The added checks retain request identity, verify one transport
  call across failure plus replay, reject unknown payloads and verify no answer.
- Full UI suite:1,120 checks in100 files passed with four workers. TypeScript/
  Vite build passed; its existing large-chunk warning remains.
- The **actual original failed installed request** was then reconciled through
  candidate source. Its original request/conversation, history logical hash,
  one request and zero answers stayed unchanged. The public reason became
  `routed_llm_quota`. Neither model selection nor generation ran again. This
  verifies failure readback, not a new successful provider call or installation.
- Hidden IAB inspection rendered the actual composer/markup/styles with an
  explicit controlled native response. Request-limit, authentication and model-
  mismatch cases all retained the draft, committed0 answers and had0 clipping.
  Screenshots were directly inspected. This is browser UI evidence, not native
  IPC/physical-input or minimized-app acceptance. The first standalone harness
  omitted the version build constant; that harness error and correction are
  retained separately from the passing normal product build.

Private evidence: `outputs/installed-input-20261023/`,
`outputs/input-failure-20261024/`. Source/tests, the failed installed runtime
attempt, candidate reconciliation, browser rendering and installed delivery
remain separate proof layers.

## Independent collection qualification hold

The current shared Kakao SDK health is still unqualified. Its latest observed
published release, [v2026.10.20](https://github.com/twoimo/oh-my-aside/releases/tag/v2026.10.20),
binds revision `9f0e1489d732120c21b6cab3e13db401eca1db32`. The current main revision
`ca303c9da5fb7bbf00c40662e89c530206775dc5` has the same relevant controller/docs/
manifest blobs as that release. The current local native controller/docs and
builtin guide differ, and three added tests are absent from the published tree.

Fifteen isolated Python checks, with native/identity/DB/subprocess/receipt-write
traps, passed14 and reproduced one existing native-mention contract failure.
This neither qualifies a new release nor proves a wrong real recipient. No
manifest was regenerated, shared source repaired, native account adopted or
source body read. The next collection gate is an authorized reviewed SDK release
followed by canonical qualification and actual standard native-CLI verification.

Current-source external adversarial/reverse verification and the installation/
activation gates remain open. The shared pstack contract keeps dependent
validation/release blocked while independent candidate work proceeds. The full
18/ST01–16/Cursor8 objective remains active.
