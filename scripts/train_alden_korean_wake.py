#!/usr/bin/env python3
"""Export a tiny Korean Alden wake classifier for openWakeWord.

This is intentionally a bounded calibration/distillation path. It reuses the
local openWakeWord audio embedding frontend, fits a regularized linear head to
one positive wake clip plus explicit non-wake and silence negatives, exports a
real ONNX model, then scores all three clips through OpenWakeVadFrontend.

Running this exporter is an explicit operator step. Output is written under
voice/models/experimental and is never bundled or auto-selected. Synthetic
results are calibration evidence only; release requires a separate
false-accept gate on representative human speech.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import wave
from pathlib import Path
from typing import Any, Sequence

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from alden_voice import CUSTOM_WAKE_MODEL_MAX_BYTES, OpenWakeVadFrontend, WAKE_THRESHOLD


SAMPLE_RATE = 16_000
FEATURE_FRAMES = 16
FEATURE_DIM = 96
NATIVE_CHUNK_SAMPLES = 1_280
FRONTEND_FRAME_SAMPLES = 320
WARMUP_WINDOWS = FEATURE_FRAMES
RIDGE_LAMBDA = 1.0
TARGET_POSITIVE_SCORE = 0.90


def _load_wav_mono_pcm16(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        sample_width = handle.getsampwidth()
        source_rate = handle.getframerate()
        raw = handle.readframes(handle.getnframes())
    if channels != 1 or sample_width != 2:
        raise ValueError(f"wake_training_wav_format_invalid:{path}")
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
    if source_rate != SAMPLE_RATE:
        output_size = round(samples.size * SAMPLE_RATE / source_rate)
        source_positions = np.arange(samples.size, dtype=np.float64)
        target_positions = np.arange(output_size, dtype=np.float64) * source_rate / SAMPLE_RATE
        samples = np.interp(target_positions, source_positions, samples)
    return np.clip(np.rint(samples), -32768, 32767).astype(np.int16)


def _feature_windows(samples: np.ndarray) -> np.ndarray:
    from openwakeword.utils import AudioFeatures

    # Use only openWakeWord's pretrained acoustic embedding pipeline. Loading
    # an unrelated stock phrase would make training depend on that detector
    # and could accidentally keep its branded wake path enabled at runtime.
    preprocessor = AudioFeatures(inference_framework="onnx", ncpu=1)
    windows: list[np.ndarray] = []
    for start in range(0, samples.size - NATIVE_CHUNK_SAMPLES + 1, NATIVE_CHUNK_SAMPLES):
        preprocessor(samples[start : start + NATIVE_CHUNK_SAMPLES])
        features = np.asarray(preprocessor.get_features(FEATURE_FRAMES), dtype=np.float32)
        if features.shape != (1, FEATURE_FRAMES, FEATURE_DIM):
            raise RuntimeError(f"wake_training_feature_shape_invalid:{features.shape}")
        windows.append(features.reshape(-1))
    if len(windows) <= WARMUP_WINDOWS:
        raise RuntimeError("wake_training_clip_too_short")
    return np.stack(windows)


def _fit_ridge_head(positive: np.ndarray, negative: np.ndarray) -> tuple[np.ndarray, float, dict[str, float]]:
    samples = np.concatenate([positive, negative], axis=0).astype(np.float32)
    mean = samples.mean(axis=0)
    scale = samples.std(axis=0) + np.float32(1e-3)
    standardized = (samples - mean) / scale
    labels = np.concatenate(
        [np.ones(positive.shape[0], dtype=np.float32), -np.ones(negative.shape[0], dtype=np.float32)]
    )

    kernel = standardized @ standardized.T
    kernel += np.eye(kernel.shape[0], dtype=np.float32) * np.float32(RIDGE_LAMBDA)
    dual = np.linalg.solve(kernel, labels)
    standardized_weight = standardized.T @ dual
    decisions = standardized @ standardized_weight
    positive_max = float(decisions[: positive.shape[0]].max())
    negative_max = float(decisions[positive.shape[0] :].max())
    if not positive_max > negative_max:
        raise RuntimeError("wake_training_not_separable")

    midpoint = (positive_max + negative_max) / 2.0
    target_logit = math.log(TARGET_POSITIVE_SCORE / (1.0 - TARGET_POSITIVE_SCORE))
    gain = target_logit / max(positive_max - midpoint, 1e-6)
    standardized_weight = standardized_weight * np.float32(gain)
    standardized_bias = np.float32(-midpoint * gain)

    raw_weight = standardized_weight / scale
    raw_bias = float(standardized_bias - np.dot(raw_weight, mean))
    return raw_weight.astype(np.float32), raw_bias, {
        "ridge_positive_max": positive_max,
        "ridge_negative_max": negative_max,
        "calibration_gain": float(gain),
    }


def _export_onnx(output: Path, weight: np.ndarray, bias: float) -> None:
    import onnx
    from onnx import TensorProto, helper, numpy_helper

    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() and output.is_symlink():
        raise RuntimeError("wake_training_output_symlink_rejected")

    flattened_size = FEATURE_FRAMES * FEATURE_DIM
    weight_matrix = weight.reshape(flattened_size, 1)
    graph = helper.make_graph(
        [
            helper.make_node("Flatten", ["features"], ["flat"], axis=1),
            helper.make_node("Gemm", ["flat", "weight", "bias"], ["logit"]),
            helper.make_node("Sigmoid", ["logit"], ["score"]),
        ],
        "alden_korean_wake_ridge",
        [helper.make_tensor_value_info("features", TensorProto.FLOAT, [1, FEATURE_FRAMES, FEATURE_DIM])],
        [helper.make_tensor_value_info("score", TensorProto.FLOAT, [1, 1])],
        [
            numpy_helper.from_array(weight_matrix, name="weight"),
            numpy_helper.from_array(np.asarray([bias], dtype=np.float32), name="bias"),
        ],
    )
    model = helper.make_model(
        graph,
        producer_name="openkakao-alden-local-wake",
        opset_imports=[helper.make_opsetid("", 13)],
    )
    model.ir_version = min(model.ir_version, 10)
    onnx.checker.check_model(model)
    onnx.save(model, str(output))
    size = output.stat().st_size
    if size <= 0 or size > CUSTOM_WAKE_MODEL_MAX_BYTES:
        output.unlink(missing_ok=True)
        raise RuntimeError("wake_training_model_size_invalid")


class _ZeroStockModel:
    def predict(self, _samples: np.ndarray) -> dict[str, float]:
        return {"alden_v0.1": 0.0}


class _NoopVad:
    def is_speech(self, _frame: bytes, _sample_rate: int) -> bool:
        return False


def _score_custom_model(model_path: Path, samples: np.ndarray) -> dict[str, Any]:
    frontend = OpenWakeVadFrontend(
        custom_wake_model=model_path,
        stock_model=_ZeroStockModel(),
        vad=_NoopVad(),
    )
    scores: list[float] = []
    usable = samples.size - (samples.size % FRONTEND_FRAME_SAMPLES)
    for start in range(0, usable, FRONTEND_FRAME_SAMPLES):
        frame = samples[start : start + FRONTEND_FRAME_SAMPLES].tobytes()
        analysis = frontend.analyze(frame)
        scores.append(float(analysis.custom_wake_score or 0.0))
    maximum = max(scores, default=0.0)
    return {
        "frames": len(scores),
        "custom_max": maximum,
        "accepted": maximum >= WAKE_THRESHOLD,
    }


def _as_paths(value: Path | Sequence[Path]) -> tuple[Path, ...]:
    if isinstance(value, Path):
        return (value,)
    paths = tuple(Path(item) for item in value)
    if not paths:
        raise ValueError("wake_training_clips_missing")
    return paths


def train_and_score(
    wake_path: Path | Sequence[Path],
    control_path: Path | Sequence[Path],
    output: Path,
) -> dict[str, Any]:
    wake_paths = _as_paths(wake_path)
    control_paths = _as_paths(control_path)
    wakes = [_load_wav_mono_pcm16(path) for path in wake_paths]
    controls = [_load_wav_mono_pcm16(path) for path in control_paths]
    silence = np.zeros(SAMPLE_RATE * 3, dtype=np.int16)

    positive = np.concatenate([_feature_windows(wake) for wake in wakes], axis=0)
    control_windows = [_feature_windows(control) for control in controls]
    silence_windows = _feature_windows(silence)
    negative = np.concatenate(
        [*control_windows, silence_windows], axis=0
    )
    weight, bias, fit = _fit_ridge_head(positive, negative)
    _export_onnx(output, weight, bias)
    wake_scores = [_score_custom_model(output, wake) for wake in wakes]
    control_scores = [_score_custom_model(output, control) for control in controls]
    silence_score = _score_custom_model(output, silence)

    return {
        "model_path": str(output),
        "model_bytes": output.stat().st_size,
        "threshold": WAKE_THRESHOLD,
        "training": {
            "method": "openwakeword_embedding_ridge_head",
            "positive_windows": int(positive.shape[0]),
            "negative_windows": int(negative.shape[0]),
            "positive_clips": len(wakes),
            "negative_clips": len(controls),
            "proof_scope": "synthesized_clips_single_system_voice",
            **fit,
        },
        "scores": {
            "wake": max(wake_scores, key=lambda score: score["custom_max"]),
            "wake_clips": wake_scores,
            "control": max(control_scores, key=lambda score: score["custom_max"]),
            "control_clips": control_scores,
            "silence": silence_score,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Train/export a bounded Korean Alden wake ONNX head.")
    parser.add_argument("--wake", type=Path, nargs="+", default=None)
    parser.add_argument("--control", type=Path, nargs="+", default=None)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "voice/models/experimental/alden_ko_ridge_candidate.onnx",
    )
    parser.add_argument("--report", type=Path, default=None)
    args = parser.parse_args(argv)

    args.output.expanduser().parent.mkdir(parents=True, exist_ok=True)

    wake_paths = args.wake or [ROOT / ".venv-voice/smoke/alden-ko.wav"]
    control_paths = args.control or [ROOT / ".venv-voice/smoke/alden-control-ko.wav"]
    report = train_and_score(
        tuple(path.expanduser() for path in wake_paths),
        tuple(path.expanduser() for path in control_paths),
        args.output.expanduser(),
    )
    encoded = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.report is not None:
        report_path = args.report.expanduser()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
