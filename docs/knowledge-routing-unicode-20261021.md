# Canonical Hangul knowledge routing — Alden 0.3.211

Text chat and voice share `_voice_knowledge_reference`. Canonically equivalent
decomposed Hangul could fail saved-source intent, project-alias and follow-up
recognition before reaching the retrieval engine's existing NFC normalization.
More seriously, a composed source cue combined with a decomposed lookup refusal
or new-input storage command could cause an unwanted knowledge lookup.

Intent and refusal checks now use NFC. Follow-up matching, query formation and
project-label aliases use the same canonical form. Query formation keeps the
original 1,024-codepoint input prefix before normalization. Returned project IDs
remain distinct. Compatibility characters are not folded, and raw messages,
history, stored sources, evidence hashes and canonical identities are preserved.

Five new cases reproduce failures against the actual installed210 modules and
cover equivalent requests, refusal/storage suppression, follow-up context,
distinct project scopes/compatibility exclusions and the original query bound.
The affected source suite passes132 tests over owned stores and transport/model
fixtures. The first alias fixture used macOS's unresolved temporary-directory
path; that path-safety error is retained and corrected by resolving the owned
fixture directory. No product path guard is relaxed.

Actual staged/installed modules, configuration preservation, previous210 recovery
bundle, signatures and exact-head CI are separate delivery receipts under
`outputs/voice-unicode211-20261021/`. Physical speech, microphone behavior and
successful live model inference remain separate gates.

An independent uninstalled retrieval/consumer candidate is described in
`evaluation/context-consumer-210-20261021/README.md`. Its literal excerpt and
coordinate changes are isolated from this runtime correction and still require
the incomplete external review, downstream/semantic evaluation and promotion
gates. Existing raw-source acquisition, semantic gold and normal release gates
remain open.
