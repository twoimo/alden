"""Responses transport for an explicitly selected OpenCodex LLM."""
from __future__ import annotations
import json
import base64
import hashlib
import os
from pathlib import Path
import stat
import time
import urllib.error
import urllib.request
import uuid
from alden_local_http import CancellableLocalResponse
from alden_model_routes import ROUTER, find_model, catalog, record_result

MAX_RESPONSE_BYTES = 1024 * 1024
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_BATCH_BYTES = 20 * 1024 * 1024


class RoutedGenerationError(RuntimeError):
    """A bounded reason; never expose provider payloads or local file paths."""


class OpenCodexLlm:
    def __init__(self, model, reasoning_effort, state_root):
        self.model, self.reasoning_effort, self.state_root = model, reasoning_effort, state_root
        self.last_metrics = {}

    def generate(self, text, token, *, history=()):
        return self._generate(text, token, history=history)

    def generate_for_turn(self, text, token, *, history=(), turn):
        return self._generate(text, token, history=history, turn=turn)

    def generate_messages(self, system_prompt, text, token, *, image_paths=(), timeout=120):
        return self._generate(text, token, history=(), system_prompt=system_prompt, image_paths=image_paths, timeout=timeout)

    def _generate(self, text, token, *, history, turn=None, system_prompt=None, image_paths=(), timeout=120):
        from alden_voice import VOICE_PERSONA_PROMPT, _voice_knowledge_reference, VOICE_CONTEXT_ITEM_MAX_CHARS, VOICE_CONTEXT_TURNS
        request_id = uuid.uuid4().hex
        context = {} if turn is None else {"conversation_id": turn.conversation_id, "turn_id": turn.turn_id,
                                          "context_version": turn.context_version, "source": turn.source}
        self.last_metrics = {"request": {**context, "local_request_id": request_id, "transport": "opencodex",
                                        "model_id": self.model, "reasoning_effort": self.reasoning_effort, "state": "running", "cancelled": False}}
        started = time.perf_counter()
        stage = "selection"
        try:
            token.raise_if_cancelled()
            selected = find_model(catalog(self.state_root), self.model)
            if not selected["selectable"] or selected["local"]:
                raise RoutedGenerationError("routed_model_not_ready")
            if self.reasoning_effort not in selected["efforts"] and self.reasoning_effort is not None:
                raise RoutedGenerationError("model_effort_not_supported")
            stage = "context"
            reference, retrieval = _voice_knowledge_reference(text, history, self.state_root, token) if system_prompt is None else ("", {"source":"caller_confirmed_context"})
            self.last_metrics["retrieval"] = retrieval
            messages = [{"role": "system", "content": VOICE_PERSONA_PROMPT if system_prompt is None else system_prompt}]
            if reference:
                messages.append({"role": "system", "content": reference})
            messages.extend({"role": item["role"], "content": item["content"][:VOICE_CONTEXT_ITEM_MAX_CHARS]}
                            for item in history[-VOICE_CONTEXT_TURNS * 2:] if item.get("role") in {"user", "assistant"} and isinstance(item.get("content"), str))
            stage = "input"
            if len(text.encode()) > 256 * 1024 or system_prompt is not None and len(system_prompt.encode()) > 64 * 1024:
                raise RoutedGenerationError("routed_llm_prompt_budget")
            user = text[:4000] if system_prompt is None else text
            if image_paths:
                parts = [{"type":"input_text", "text":user}]
                if len(image_paths) > 4:
                    raise RoutedGenerationError("image_input_budget")
                total_image_bytes = 0
                self.last_metrics["images"] = []
                for path in image_paths:
                    token.raise_if_cancelled()
                    data, mime = self._image_snapshot(path)
                    total_image_bytes += len(data)
                    if total_image_bytes > MAX_IMAGE_BATCH_BYTES:
                        raise RoutedGenerationError("image_input_budget")
                    self.last_metrics["images"].append({"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "mime": mime})
                    parts.append({"type":"input_image","image_url":"data:"+mime+";base64,"+base64.b64encode(data).decode()})
                user = parts
            messages.append({"role": "user", "content": user})
            body = {"model": self.model, "input": messages, "max_output_tokens": 1024, "stream": True, "store": False}
            if self.reasoning_effort:
                body["reasoning"] = {"effort": self.reasoning_effort}
            headers = {"Content-Type": "application/json", "Accept": "text/event-stream", "X-Alden-Request-Id": request_id}
            for key in ["conversation_id", "turn_id", "context_version"]:
                if key in context:
                    headers["X-Alden-" + key.replace("_", "-")] = str(context[key])
            request = urllib.request.Request(ROUTER + "/responses", json.dumps(body, ensure_ascii=False).encode(), headers)
            output, total, completed = [], 0, False
            token.raise_if_cancelled()
            stage = "transport"
            with CancellableLocalResponse(request, min(120, max(1, timeout)), token, routed=True) as response:
                stage = "response"
                if "text/event-stream" not in response.headers.get("Content-Type", ""):
                    raw = response.read(MAX_RESPONSE_BYTES + 1)
                    if len(raw) > MAX_RESPONSE_BYTES:
                        raise RoutedGenerationError("routed_llm_response_budget")
                    payload = self._json(raw)
                    self._confirm_completed(payload)
                    output = self._text(payload)
                    if output:
                        self.last_metrics["ttft_ms"] = (time.perf_counter() - started) * 1000
                    completed = True
                else:
                    while True:
                        token.raise_if_cancelled()
                        line = response.readline(65537)
                        if not line:
                            break
                        total += len(line)
                        if len(line) > 65536 or total > MAX_RESPONSE_BYTES:
                            raise RoutedGenerationError("routed_llm_response_budget")
                        if not line.startswith(b"data:"):
                            continue
                        data = line[5:].strip()
                        if data == b"[DONE]":
                            break
                        event = self._json(data)
                        kind = event.get("type")
                        if kind == "response.output_text.delta":
                            delta = event.get("delta")
                            if isinstance(delta, str):
                                if not output:
                                    self.last_metrics["ttft_ms"] = (time.perf_counter() - started) * 1000
                                output.append(delta)
                        elif kind == "response.completed":
                            payload = event.get("response", {})
                            self._confirm_completed(payload)
                            terminal_output = self._text(payload)
                            if terminal_output:
                                output = terminal_output
                            completed = True
                        elif kind in {"error", "response.failed", "response.incomplete"}:
                            raise RoutedGenerationError("routed_llm_generation_failed")
            token.raise_if_cancelled()
            result = "".join(output).strip()
            if not completed or not result:
                raise RoutedGenerationError("routed_llm_incomplete_response")
            self.last_metrics["stage"] = "completed"
            self.last_metrics["request"]["state"] = "completed"
            record_result(self.state_root, self.model, success=True)
            return result
        except Exception as error:
            cancelled = token.is_cancelled()
            self.last_metrics["request"].update(state="cancelled" if cancelled else "failed", cancelled=cancelled)
            code = "cancelled" if cancelled else "quota" if isinstance(error, urllib.error.HTTPError) and error.code == 429 else "authentication" if isinstance(error, urllib.error.HTTPError) and error.code in {401, 403} else "unavailable"
            reason = str(error) if isinstance(error, RoutedGenerationError) else "routed_llm_" + code
            self.last_metrics.update(stage=stage, error_code=reason)
            # A missing image, bad prompt or context failure says nothing about
            # provider readiness and must not poison the model picker indicator.
            if stage in {"transport", "response"}:
                record_result(self.state_root, self.model, success=False, error=code)
            token.raise_if_cancelled()
            if isinstance(error, RoutedGenerationError):
                raise
            raise RoutedGenerationError(reason) from error
        finally:
            self.last_metrics["elapsed_ms"] = (time.perf_counter() - started) * 1000

    @staticmethod
    def _json(raw):
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeError) as error:
            raise RoutedGenerationError("routed_llm_invalid_response") from error
        if not isinstance(payload, dict):
            raise RoutedGenerationError("routed_llm_invalid_response")
        return payload

    def _confirm_completed(self, payload):
        if not isinstance(payload, dict):
            raise RoutedGenerationError("routed_llm_invalid_response")
        returned = payload.get("model")
        if not isinstance(returned, str) or returned not in {self.model, self.model.split("/", 1)[-1]}:
            raise RoutedGenerationError("routed_model_mismatch")
        if payload.get("status") in {"failed", "incomplete", "cancelled"} or payload.get("error") is not None or payload.get("incomplete_details") is not None:
            raise RoutedGenerationError("routed_llm_generation_failed")
        if payload.get("status") != "completed":
            raise RoutedGenerationError("routed_llm_incomplete_response")
        self.last_metrics["usage"] = payload.get("usage")
        self.last_metrics["backend_request_id"] = payload.get("id")

    @staticmethod
    def _text(payload):
        items = payload.get("output", [])
        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
            raise RoutedGenerationError("routed_llm_invalid_response")
        output = []
        for item in items:
            if item.get("type") != "message" or item.get("role") != "assistant":
                continue
            content = item.get("content", [])
            if not isinstance(content, list) or any(not isinstance(part, dict) for part in content):
                raise RoutedGenerationError("routed_llm_invalid_response")
            output.extend(part["text"] for part in content if part.get("type") == "output_text" and isinstance(part.get("text"), str))
        return output

    @staticmethod
    def _image_snapshot(path):
        """Bind MIME, digest and payload to one bounded regular-file snapshot.

        This checks format signatures, not a full pixel decode. The caller's
        attachment provenance checks and the provider decoder remain separate.
        """
        path = Path(path)
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(fd, "rb") as handle:
                before = os.fstat(handle.fileno())
                if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= MAX_IMAGE_BYTES:
                    raise RoutedGenerationError("image_input_unavailable")
                data = handle.read(MAX_IMAGE_BYTES + 1)
                after = os.fstat(handle.fileno())
                current = path.lstat()
            if ((before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                    or (after.st_size, after.st_mtime_ns, after.st_ctime_ns) != (current.st_size, current.st_mtime_ns, current.st_ctime_ns)
                    or (before.st_dev, before.st_ino) != (current.st_dev, current.st_ino)
                    or len(data) != before.st_size):
                raise RoutedGenerationError("image_input_unavailable")
        except OSError as error:
            raise RoutedGenerationError("image_input_unavailable") from error
        if len(data) >= 24 and data.startswith(b"\x89PNG\r\n\x1a\n") and data[12:16] == b"IHDR":
            mime = "image/png"
        elif len(data) >= 4 and data.startswith(b"\xff\xd8\xff"):
            mime = "image/jpeg"
        elif len(data) >= 10 and data[:6] in {b"GIF87a", b"GIF89a"}:
            mime = "image/gif"
        elif len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            mime = "image/webp"
        else:
            raise RoutedGenerationError("image_input_unavailable")
        return data, mime
