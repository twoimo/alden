"""Explicit user-selected OpenCodex routes and observed model availability.

Catalog metadata is data: never retain model_messages, tools, or instructions.
No discovery call loads weights or generates text. Automatic selection occurs
before a turn; a failed generation never retries with a different model.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time
import urllib.request

ROUTER = "http://127.0.0.1:10101/v1"
DEFAULT_MODEL = "google-antigravity/gemini-3.8-flash"
DEFAULT_EFFORT = "high"
EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"}
MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/:-]{0,199}\Z")
MAX_BYTES = 4 * 1024 * 1024

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("model_router_redirect_rejected")

def _read(url):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(url, timeout=3) as response:
        raw = response.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise RuntimeError("model_catalog_budget")
    return json.loads(raw)

def _state(root, with_hash=False):
    path = Path(root) / "reply-model.json"
    if path.is_symlink():
        raise RuntimeError("model_configuration_invalid")
    if not path.exists():
        return ({}, None) if with_hash else {}
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > 8192:
            raise RuntimeError("model_configuration_invalid")
        raw = handle.read(8193)
    if len(raw) > 8192:
        raise RuntimeError("model_configuration_invalid")
    state = json.loads(raw)
    if not isinstance(state, dict):
        raise RuntimeError("model_configuration_invalid")
    return (state, hashlib.sha256(raw).hexdigest()) if with_hash else state

def _health(root):
    try:
        path = Path(root) / "model-route-health.json"
        if path.is_symlink() or path.stat().st_size > 128 * 1024:
            return {}
        value = json.loads(path.read_bytes())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}

def _write(path, value):
    path = Path(path)
    if path.is_symlink():
        raise RuntimeError("model_configuration_invalid")
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(value, handle, ensure_ascii=False)
            handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

def record_result(root, model, *, success, error=None):
    if root is None:
        return
    health = _health(root)
    health[model] = {"at": time.time(), "success": success is True,
                     "error": error if error in {"quota", "authentication", "memory", "unavailable", "cancelled"} else None}
    try:
        _write(Path(root) / "model-route-health.json", dict(list(health.items())[-256:]))
        return True
    except (OSError, RuntimeError):
        return False  # Telemetry failure must not discard a confirmed answer.

def host_workspace():
    try:
        from alden_voice import _read_voice_memory_budget
        budget = _read_voice_memory_budget()
        return "ready" if budget.pressure_level == 1 and budget.reclaimable_bytes >= 2 * 1024**3 and budget.free_physical_bytes >= 128 * 1024**2 else "memory"
    except Exception:
        return "memory_unknown"

def catalog(root, *, fetcher=_read, now=None, memory_reader=host_workspace):
    stamp = time.time() if now is None else now
    raw = fetcher(ROUTER + "/models")
    rows = raw.get("models", raw.get("data", [])) if isinstance(raw, dict) else []
    if not isinstance(rows, list) or len(rows) > 256:
        raise RuntimeError("model_catalog_invalid")
    health, seen, models = _health(root), set(), []
    local_rows = []
    for port in [11234, 11235]:
        try:
            values = fetcher(f"http://127.0.0.1:{port}/v1/models").get("data", [])
            local_rows.extend(row for row in values if isinstance(row, dict) and row.get("loaded") is True and row.get("state") == "ready")
        except Exception:
            pass
    workspace = memory_reader() if local_rows else "memory_unknown"
    for row in rows:
        if not isinstance(row, dict):
            continue
        model = row.get("slug") or row.get("id")
        if not isinstance(model, str) or not MODEL_ID.fullmatch(model) or model in seen:
            continue
        seen.add(model)
        local = model.startswith("mlx/")
        label = row.get("display_name") or row.get("label") or model
        levels = row.get("supported_reasoning_levels") or row.get("reasoning_efforts") or []
        efforts = []
        for item in levels if isinstance(levels, list) else []:
            value = item if isinstance(item, str) else item.get("effort", item.get("value")) if isinstance(item, dict) else None
            if value in EFFORTS and value not in efforts:
                efforts.append(value)
        observation = health.get(model, {})
        fresh = 0 <= stamp - observation.get("at", 0) <= 300
        ready = any(str(item.get("id", "")).removeprefix("mlx/") == model.removeprefix("mlx/") for item in local_rows)
        status = ("running" if workspace == "ready" else workspace) if local and ready else "not_loaded" if local else "connected"
        if fresh and observation.get("success") is True and (not local or ready and workspace == "ready"):
            status = "available"
        elif fresh and observation.get("success") is False and observation.get("error") != "cancelled":
            status = observation.get("error") or "unavailable"
        default = row.get("default_reasoning_level", row.get("reasoning_effort"))
        if "tts" in model.lower():
            status = "not_chat"
        models.append({"id": model, "label": str(label)[:160], "provider": model.split("/")[0] if "/" in model else "openai",
                       "local": local, "efforts": efforts, "default_effort": default if default in efforts else "medium" if "medium" in efforts else next(iter(efforts), None),
                       "status": status, "selectable": (not local or ready and workspace == "ready") and "tts" not in model.lower(),
                       "automatic_candidate": status == "available" and model != "gpt-6-astra" and "tts" not in model.lower()})
    state = _state(root)
    preview = automatic_choice(models)
    return {"ok": True, "automatic_model": preview["id"] if preview else None, "models": models, "model": state.get("model", DEFAULT_MODEL),
            "reasoning_effort": state.get("reasoning_effort", DEFAULT_EFFORT if state.get("model", DEFAULT_MODEL) == DEFAULT_MODEL else None), "mode": state.get("mode", "manual"),
            "observed_at": stamp, "local_running": sum(item["status"] in {"running", "available"} and item["local"] for item in models)}

def find_model(payload, requested):
    alias = "google-antigravity/" + requested[4:] if requested.startswith("agy/") else requested
    matches = [row for row in payload["models"] if row["id"] == alias]
    if len(matches) != 1:
        raise RuntimeError("model_not_in_current_catalog")
    return matches[0]

def save(root, options, *, fetcher=_read):
    if not isinstance(options, dict) or options.get("mode", "manual") not in {"manual", "automatic"}:
        raise ValueError("model_selection_invalid")
    payload = catalog(root, fetcher=fetcher)
    requested = options.get("model", DEFAULT_MODEL)
    if not isinstance(requested, str):
        raise ValueError("model_selection_invalid")
    chosen = find_model(payload, requested)
    effort = options.get("reasoning_effort", chosen["default_effort"])
    if effort is not None and effort not in chosen["efforts"]:
        raise ValueError("model_effort_not_supported")
    if not chosen["selectable"]:
        raise RuntimeError("local_model_not_ready")
    state = {"schema_version": 1, "model": chosen["id"], "reasoning_effort": effort,
             "mode": options.get("mode", "manual"), "transport": "local" if chosen["local"] else "opencodex",
             "updated_at": int(time.time())}
    _write(Path(root) / "reply-model.json", state)
    if _state(root) != state:
        raise RuntimeError("model_selection_readback_failed")
    return {"ok": True, "stored": True, "action": "routed-model-set", **state}

def automatic_choice(models):
    candidates = [row for row in models if row["automatic_candidate"]
                  and ("Qwen3.8-27B" in row["id"] or row["id"] == DEFAULT_MODEL)]
    if candidates:
        return sorted(candidates, key=lambda row: not row["local"])[0]
    return next((row for row in models if row["id"] == DEFAULT_MODEL and row["status"] in {"available", "connected"}), None)

def selection(root):
    state, configuration_sha = _state(root, True)
    payload = catalog(root)
    chosen = find_model(payload, state.get("model", DEFAULT_MODEL))
    if state.get("mode") == "automatic":
        # Explicitly conservative: never escalate into Astra/Pro/Opus or load
        # weights. Prefer a recently verified local Qwen, then the user's Flash.
        chosen = automatic_choice(payload["models"])
        if chosen is None:
            raise RuntimeError("automatic_model_unavailable")
    if not chosen["selectable"]:
        raise RuntimeError("local_model_not_ready")
    if state.get("mode") == "automatic" and chosen["status"] not in {"available", "running", "connected"}:
        raise RuntimeError("automatic_model_unavailable")
    effort = state.get("reasoning_effort", DEFAULT_EFFORT)
    if effort is not None and effort not in chosen["efforts"]:
        if state.get("mode") != "automatic":
            raise RuntimeError("model_effort_not_supported")
        effort = chosen["default_effort"]
    return {"model": chosen["id"], "reasoning_effort": effort, "transport": "local" if chosen["local"] else "opencodex",
            "base_url": ("http://127.0.0.1:11235/v1" if "Flash-Next-MLX-Serve-iQ" in chosen["id"] else "http://127.0.0.1:11234/v1") if chosen["local"] else ROUTER, "mode": state.get("mode", "manual"), "source": "saved_configuration",
            "configuration_sha256": configuration_sha}
