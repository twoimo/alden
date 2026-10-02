import type { RuntimeSnapshot } from "./contracts";

/** The status RMS belongs to microphone input, including during TTS playback. */
export function freshInputRms(snapshot: RuntimeSnapshot | null, nowSeconds = Date.now() / 1000): number {
  const voice = snapshot?.voice;
  if (!Number.isFinite(nowSeconds) || !snapshot?.available || !voice?.available || voice.errorCode
    || (voice.state !== "user_listen" && voice.state !== "wake_listen")
    || !Number.isFinite(voice.updatedAt) || voice.updatedAt <= 0
    || voice.updatedAt > nowSeconds + 1 || nowSeconds - voice.updatedAt > 3) return 0;
  return Number.isFinite(voice.rms) ? Math.max(0, Math.min(1, voice.rms)) : 0;
}
