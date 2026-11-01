# Native input qualification and playback — 0.3.33

## The actual zero-input cause

The0.3.32 installed audio library reported authorized microphone access(3), ABI2 and echo processing active. An input-only sample received51 complete frames with no overflow, but every sample was zero. Three normal recorded-TTS playback trials also received zero-valued microphone frames. These observations do not prove self-echo suppression, real speech capture or human interruptions.

Current device readback identified the built-in MacBook Pro microphone and speakers. IORegistry reported `AppleClamshellState=Yes`. [Apple's hardware security description](https://support.apple.com/guide/security/hardware-microphone-disconnect-secbbd20b00b/web) states that Apple silicon laptop microphones are physically disconnected while the lid is closed. Authorization and a running voice-processing engine therefore cannot establish usable input. The physical lid must be opened or an external microphone selected; no software bypass or device preference change was attempted.

## Runtime correction

Native audio ABI3 separately exposes input status. It reads the CoreAudio default input transport and IOPM lid state, caches observation for at most1s, and rejects a changed default input route against the original captured device identity. Closed-lid built-in input gets a specific hardware-blocked status; a USB microphone is not blocked merely because the lid is closed. Unverifiable input state stays distinct.

The microphone poller rejects that status before reading zero buffers. A live session returns an actionable error before publishing a listening state or consuming speech. The UI tells the user to open the lid or connect an external microphone, rather than misreporting permission denial, STT misunderstanding or silence. Output-only playback remains available: this guard does not disable speaker playback or weaken hardware protections. The packaged native library and Python adapter advance together to ABI3; older ABIs are rejected before audio actions.

Swift contract cases cover open/closed built-in input, closed-lid USB input, unknown transport/lid information and route change. Python regression cases cover no frame consumption, a specific session error and owned playback cleanup. The native offline DSP/player-clock test still has zero mismatches across7 windows; it is not a live microphone or acoustic test. CI now runs those native tests. UI385, focused voice58 and desktop Rust108/Clippy checks passed in the local source.

## Real playback and cancellation evidence

The actual installed Qwen3-TTS adapter generated a2.96s,24kHz mono PCM16 controlled Korean sentence using `Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice`, bf16, Sohee and MPS. The resolved cache snapshot was `0c0e3051f131929182e2c023b9537f8b1c68adfe`. One cold generation/close observation took16.87s; it is not a percentile or an end-of-utterance pipeline latency. Optional SoX/flash-attn warnings did not prevent waveform generation; no dependency or model substitution was made.

Candidate ABI3 playback retained output while classifying input as hardware blocked. Three normal playback trials each observed positive rendered output RMS(peak0.17597). The original timed-cancel probe anchored to engine readiness, and one trial canceled before any rendered output. Its results are preserved as unqualified cancellation timing.

The probe now anchors cancellation0.4s after the first positive rendered-output window and requires output observation for success. Three qualified trials had30/31/31 positive output samples and cancellation waiter delays1.83/2.45/8.71ms(median2.45,max8.71,n=3). Different anchors prevent a paired speedup claim. These delays measure the owned playback waiter, not acoustic speaker-tail latency, global shortcut response or a human interruption.

The measurement separately reports hardware input reason, actual input activity and output observation. It never qualifies echo behavior from all-zero/disconnected input. Microphone raw data/transcripts are not persisted. A human availability question remains pending; no human barge-in was fabricated or inferred. Wakeword, live STT→LLM→TTS interaction, echo with usable near-end input, global emergency shortcut, Retina, full graph/MCP/scheduler/DPO and signed production remain full-goal gates.

## Installed delivery and remaining physical input

The final verification sourcef1170d4 passed all4 CI jobs. Its change from runtime0512245 only corrected the existing native SDK test compiler's source list; the first051 CI had3 successful jobs and one missing-source compile failure, preserved as failure evidence. The redundant extra native test step was removed; the existing test now exercises input qualification and offline playback together. The expanded local voice/native group has66 passing tests.

Normal installation produced0.3.33 with47 files equal to its candidate,3 unchanged configurations, preserved27B/embedding services and strict/deep ad-hoc signature. The actual installed manual-session path in an isolated status root returned `error`/`mic_hardware_lid_closed` before listening. Installed output still rendered35 positive windows; its qualified cancellation waiter delay was3.61ms(n=1). This proves the packaged output/qualification behavior, not near-end capture or physical interruption.

The actual installed local STT adapter also transcribed the controlled TTS reference as `올든 음성 연결을 확인합니다.` in0.974s(n=1). This is file-input inference with a known generated sentence. It does not substitute for human microphone testing. The fresh hardware readback still had a closed lid. Human readiness remains pending; no input/device setting or security protection was changed.
