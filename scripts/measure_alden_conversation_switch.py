#!/usr/bin/env python3
"""Paired conversation-switch races using synthetic adapters and private DBs.

Measures the control path only: no LLM/STT/TTS, microphone, network or sends.
The before module is read from Git; source files and global abort state are untouched.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import platform
import statistics
import subprocess
import sys
from tempfile import TemporaryDirectory
import threading
import time

from alden_abort import AbortController
from alden_history import read, record_voice


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def sample(module, root: Path) -> dict:
    root.mkdir(mode=0o700)
    destination = "b" * 32
    record_voice(root, destination, 4, "user", "새 대화의 질문", 7)
    record_voice(root, destination, 4, "assistant", "새 대화의 답", 8)
    entered, release = threading.Event(), threading.Event()
    spoken, results, failures = [], [], []

    class Model:
        def generate(self, *_args, **_kwargs):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("measurement gate timed out")
            return "이전 대화의 늦은 응답"

    class Speech:
        def speak(self, text, token):
            token.raise_if_cancelled()
            spoken.append(text)

    pipeline = module.AldenVoicePipeline(
        stt=object(), llm=Model(), tts=Speech(), token=AbortController(root).token(),
        status=module.VoiceStatusStore(root), manual_listen=True,
    )
    original = pipeline.conversation_id

    def generate():
        try:
            results.append(pipeline.process_text("이전 대화의 질문"))
        except Exception as error:
            failures.append(type(error).__name__)

    worker = threading.Thread(target=generate)
    worker.start()
    try:
        if not entered.wait(5):
            raise RuntimeError("inference entry not observed")
        started = time.perf_counter_ns()
        pipeline.restore_selected_conversation(destination)
        switch_ms = (time.perf_counter_ns() - started) / 1e6
        release.set()
        worker.join(5)
        if worker.is_alive() or failures or len(results) != 1:
            raise RuntimeError(f"measurement worker failed: {failures}")
        result = results[0]
        items = read(root, Path("/unused"), "voice-history-messages", chat_id=destination)["items"]
        return {
            "switch_ms": switch_ms,
            "late_playback": len(spoken),
            "late_answer_in_destination": sum(row["content"] == "이전 대화의 늦은 응답" for row in items),
            "wrong_result_conversation": int(result.conversation_id != original),
            "cancelled_result": int(result.cancelled),
            "cancelled_reply_exposed": int(result.cancelled and bool(result.reply)),
        }
    finally:
        release.set()
        worker.join(5)
        pipeline.close()


def summary(rows: list[dict]) -> dict:
    times = sorted(row["switch_ms"] for row in rows)
    return {
        "n": len(rows), "switch_ms_p50": statistics.median(times),
        "switch_ms_p95": times[math.ceil(.95 * len(times)) - 1],
        **{key: sum(row[key] for row in rows) for key in rows[0] if key != "switch_ms"},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-ref", required=True)
    parser.add_argument("--after-module", type=Path, default=Path(__file__).with_name("alden_voice.py"))
    parser.add_argument("--pairs", type=int, default=30)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.pairs <= 200 or args.out.exists():
        parser.error("pairs must be 1..200 and output must be new")
    repo = Path(__file__).resolve().parents[1]
    baseline = subprocess.check_output(["git", "show", f"{args.baseline_ref}:scripts/alden_voice.py"], cwd=repo)
    after = args.after_module.resolve()
    raw = {"before": [], "after": []}
    with TemporaryDirectory(prefix="alden-conversation-switch-") as temp:
        root = Path(temp)
        before = root / "before.py"
        before.write_bytes(baseline)
        modules = {"before": load_module("alden_voice_measure_before", before),
                   "after": load_module("alden_voice_measure_after", after)}
        for pair in range(args.pairs):
            order = ("before", "after") if pair % 2 == 0 else ("after", "before")
            for arm in order:
                raw[arm].append(sample(modules[arm], root / f"{arm}-{pair:03}"))
    report = {
        "schema_version": 1, "python": platform.python_version(), "machine": platform.machine(),
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "baseline_ref": args.baseline_ref,
        "before_sha256": hashlib.sha256(baseline).hexdigest(),
        "after_sha256": hashlib.sha256(after.read_bytes()).hexdigest(),
        "order": "alternating paired order; fresh synthetic database per arm",
        "summary": {arm: summary(rows) for arm, rows in raw.items()}, "samples": raw,
        "limits": ["Controlled blocked generation, not natural dialogue or physical barge-in",
                   "Switch time includes private SQLite history read and status publication",
                   "No LLM, voice hardware, network, Kakao send or global abort changes",
                   "Other host workloads were preserved; no whole-app latency or power claim"],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report["summary"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
