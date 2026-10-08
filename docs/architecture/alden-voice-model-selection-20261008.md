# Saved local model selection for voice — 0.3.34

The microphone and file voice pipelines previously constructed a default 27B adapter. Changing `reply-model.json` therefore did not change their inference model. Explicit adapters already supported the mixed 4–8 Flash variant; the iQ variant and its separate service were missing from voice.

Both pipelines now read one bounded, regular, non-symlink configuration snapshot at the start of each LLM turn. Prefixless and `mlx/` names resolve only to the three existing fixed local IDs. 27B and mixed Flash use `127.0.0.1:11234`; Flash iQ uses `127.0.0.1:11235`. The chosen model, service and configuration hash remain fixed for that request. A later turn reads the current saved choice. This does not write settings, prepare weights, change a provider or select an alternative model.

An absent configuration uses the documented 27B default. An existing invalid or unsupported choice fails before transport. The exact selected model must appear once as loaded and ready in its own service catalog. A stopped or unprepared service returns an actionable error; a ready model on the other service cannot satisfy that check. The cancellable transport keeps its localhost, exact-port, route and redirect restrictions. Embedding remains on its existing separate service.

Nine focused regression cases cover fixed routes and aliases, in-flight configuration replacement, missing/invalid/remote/symlink/FIFO configuration, unprepared iQ without fallback, exact-port transport, cancelled-turn metrics and failed-pipeline history/playback. The expanded affected voice suite passes 88 tests and the UI suite passes 385. Fixture responses for iQ verify the protocol only and do not establish loaded iQ weights or inference.

The actual configured 27B adapter returned `확인` in 0.5211 s (one diagnostic text request; no microphone, playback or user-history write). The actual unavailable iQ path made only `GET /v1/models` on port 11235, returned `local_llm_model_not_ready` and left the loaded 27B model and all three settings files unchanged. See the [bounded source readback](alden-voice-model-selection-readback-20261008.json). These samples establish routing and failure behavior, not latency percentiles or a speedup.

Flash iQ remains blocked by its previously observed effective-memory requirement; no new load was attempted. Its exact local weight provenance remains pending. Physical human voice, wake/echo/barge-in, global emergency shortcut, collection/MCP/scheduler, retrieval evaluation, independent DPO and signed release/production remain part of the active full goal. Local adapter tests do not complete them.

## Installed readback

Runtime source `c071b60c664aff6aa9e7db009b9fa4ab5d97fbf9` passed all four CI jobs. The normal installer delivered 0.3.34 to `/Applications/Alden.app`; all 47 files equal its candidate, strict/deep ad-hoc signature passes and three settings match their pre-cutover backups. The managed primary is PID79496; the existing model63062 and embedding11016 remained alive. No unrelated process or configuration was changed.

The installed adapter returned `확인` in 0.4905 s for one fully instrumented diagnostic request. An earlier successful response `확인.` failed the diagnostic driver's overly strict punctuation assertion before metrics were persisted; after correcting that validator, the full installed checks passed. There were two generation attempts and one timed sample, not a percentile or before/after improvement. The installed unavailable iQ path still performed only its own catalog GET and no fallback generation.

Backup inspection corrected a preparation assumption: the normal installer's retained backup contains LaunchAgent files, not a full previous app archive. All three settings were separately backed up before cutover; the prior 0.3.33 source and manifest remain recorded, but its exact app archive was not retained in this cutover. The delivered 0.3.34 app was copied and hash-verified as a private recovery snapshot. Future cutovers need an explicit full previous-app snapshot rather than assuming the installer retains one. This remains release-workflow work within the active goal.
