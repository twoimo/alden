# Alden 0.3.151 context and MCP boundaries

Runtime source `106b5e07f8cffc9af0cf7a175c376d1d78f0ed5d` was installed with the existing Apple Development identity before the zoom-label follow-up. It fixes three independently reproduced boundaries:

- Voice restoration excludes an explicitly retracted text request by exact conversation and turn. Legacy voice-only history remains supported; all-history turn/context maxima prevent ID reuse. Already confirmed pending/cancelled inputs are preserved unless explicitly retracted.
- A missing model configuration selects the common Gemini Flash/high/manual OpenCodex default without creating a settings file. Invalid existing settings still fail specifically; saved local choices remain intact.
- Knowledge MCP history filters project metadata to the current request scope. Other legitimate projects remain visible in the collection UI's global catalog. Source/provenance filters and counts retain their existing boundaries.

Main Python verification covered274 distinct cases;269 passed in the menu runtime and five NumPy-dependent cases were skipped there, then all five passed in a35-case voice-runtime run. Rust146 and strict Clippy passed. [Exact-source CI39144492113](https://github.com/twoimo/alden/actions/runs/39144492113) passed all four jobs. The actual owned loopback cancellation test passed without a model or audio.

Both candidate and installed packaged modules restored real isolated SQLite history and served an actual separate stdio MCP process. Seven RPC replies covered initialization, tool discovery, two separate scopes, combined scope, graph read and outside-scope rejection. Global UI metadata and fixture source bytes were preserved. This is not proof of two independent globally configured knowledge-MCP hosts.

One installed actual default generation answered the public fixture `42` through `google-antigravity/gemini-3.8-flash`, high. The selected route and response model were reconciled; no saved configuration was created. This proves the bounded default path, not the whole catalog. No microphone, TTS, original conversation or sending queue was used.

All77 installed files/modes matched the canonical candidate, strict deep signature passed, and the independent0.3.150 recovery plus model/enrollment/emergency settings, seven targets and acquisition policies reconciled. Raw evidence remains under `outputs/context-mcp-151-20261019/`, `outputs/voice-context-20261019/` and `outputs/mcp-scope-20261019/`. The [nine implementation diagrams](architecture/alden-0.3.151/README.md) describe the same source. External Gemini adversarial and final reverse review remain incomplete; no blanket cross-audit completion is claimed.
