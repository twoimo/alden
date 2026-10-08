"""Responses transport for an explicitly selected OpenCodex LLM."""
from __future__ import annotations
import json
import time
import urllib.error
import urllib.request
import uuid
from alden_local_http import CancellableLocalResponse
from alden_model_routes import ROUTER, find_model, catalog, record_result

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
        try:
            token.raise_if_cancelled()
            selected = find_model(catalog(self.state_root), self.model)
            if not selected["selectable"] or selected["local"]:
                raise RuntimeError("routed_model_not_ready")
            if self.reasoning_effort not in selected["efforts"] and self.reasoning_effort is not None:
                raise RuntimeError("model_effort_not_supported")
            reference, retrieval = _voice_knowledge_reference(text, history, self.state_root, token) if system_prompt is None else ("", {"source":"caller_confirmed_context"})
            self.last_metrics["retrieval"] = retrieval
            messages = [{"role": "system", "content": VOICE_PERSONA_PROMPT if system_prompt is None else system_prompt}]
            if reference:
                messages.append({"role": "system", "content": reference})
            messages.extend({"role": item["role"], "content": item["content"][:VOICE_CONTEXT_ITEM_MAX_CHARS]}
                            for item in history[-VOICE_CONTEXT_TURNS * 2:] if item.get("role") in {"user", "assistant"} and isinstance(item.get("content"), str))
            if len(text.encode()) > 256 * 1024 or system_prompt is not None and len(system_prompt.encode()) > 64 * 1024:
                raise RuntimeError("routed_llm_prompt_budget")
            user = text[:4000] if system_prompt is None else text
            if image_paths:
                import base64, mimetypes
                from pathlib import Path
                parts = [{"type":"input_text", "text":user}]
                if len(image_paths) > 4:
                    raise RuntimeError("image_input_budget")
                for path in image_paths:
                    path = Path(path)
                    if not path.is_file() or path.stat().st_size > 8 * 1024 * 1024:
                        raise RuntimeError("image_input_unavailable")
                    mime = mimetypes.guess_type(path.name)[0]
                    if mime not in {"image/png","image/jpeg","image/webp","image/gif"}:
                        raise RuntimeError("image_input_unavailable")
                    parts.append({"type":"input_image","image_url":"data:"+mime+";base64,"+base64.b64encode(path.read_bytes()).decode()})
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
            with CancellableLocalResponse(request, min(120, max(1, timeout)), token, routed=True) as response:
                if "text/event-stream" not in response.headers.get("Content-Type", ""):
                    payload = json.loads(response.read(1024 * 1024 + 1))
                    output = self._text(payload); completed = True
                else:
                    while True:
                        token.raise_if_cancelled()
                        line = response.readline(65537)
                        if not line:
                            break
                        total += len(line)
                        if len(line) > 65536 or total > 1024 * 1024:
                            raise RuntimeError("routed_llm_response_budget")
                        if not line.startswith(b"data:"):
                            continue
                        data = line[5:].strip()
                        if data == b"[DONE]":
                            break
                        event = json.loads(data)
                        kind = event.get("type")
                        if kind == "response.output_text.delta":
                            delta = event.get("delta")
                            if isinstance(delta, str):
                                if not output:
                                    self.last_metrics["ttft_ms"] = (time.perf_counter() - started) * 1000
                                output.append(delta)
                        elif kind == "response.completed":
                            payload = event.get("response", {})
                            returned = payload.get("model")
                            if returned and returned not in {self.model, self.model.split("/", 1)[-1]}:
                                raise RuntimeError("routed_model_mismatch")
                            self.last_metrics["usage"] = payload.get("usage")
                            self.last_metrics["backend_request_id"] = payload.get("id")
                            if not output:
                                output = self._text(payload)
                            completed = True
                        elif kind in {"error", "response.failed", "response.incomplete"}:
                            raise RuntimeError("routed_llm_generation_failed")
            token.raise_if_cancelled()
            result = "".join(output).strip()
            if not completed or not result:
                raise RuntimeError("routed_llm_incomplete_response")
            self.last_metrics["request"]["state"] = "completed"
            record_result(self.state_root, self.model, success=True)
            return result
        except Exception as error:
            cancelled = token.is_cancelled()
            self.last_metrics["request"].update(state="cancelled" if cancelled else "failed", cancelled=cancelled)
            code = "cancelled" if cancelled else "quota" if isinstance(error, urllib.error.HTTPError) and error.code == 429 else "authentication" if isinstance(error, urllib.error.HTTPError) and error.code in {401, 403} else "unavailable"
            record_result(self.state_root, self.model, success=False, error=code)
            token.raise_if_cancelled()
            raise RuntimeError("routed_llm_" + code) from error
        finally:
            self.last_metrics["elapsed_ms"] = (time.perf_counter() - started) * 1000

    @staticmethod
    def _text(payload):
        return [part["text"] for item in payload.get("output", []) if item.get("type") == "message" and item.get("role") == "assistant"
                for part in item.get("content", []) if part.get("type") == "output_text" and isinstance(part.get("text"), str)]
