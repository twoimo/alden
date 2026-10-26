"""Pure validation for the one-read, nonstream local knowledge tool protocol.

The caller owns transport, the raw MAX_RESPONSE_BYTES cap, strict decoding of
the outer JSON (including duplicate-key rejection), and tool execution. This
module accepts a decoded completion only; it neither imports the runtime nor
derives tool authority from model text. Returned messages contain only the
validated assistant content and, for a tool request, one canonical empty call.
"""

from __future__ import annotations

import json
import re


TOOL_NAME = "lookup_requested_knowledge"
MAX_RESPONSE_BYTES = 65536
_MAX_ARGUMENT_BYTES = 256
_MAX_COUNTER = 9_007_199_254_740_991
_INVALID = "local_llm_tool_protocol_invalid"
_TRUNCATED = "local_llm_reply_truncated"
_INCOMPLETE = "local_llm_incomplete_response"
_ERROR_CODES = frozenset({_INVALID, _TRUNCATED, _INCOMPLETE})
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+:/-]{0,127}\Z")
_SECRET_PREFIXES = ("sk-", "ghp_", "github_pat_", "xox", "bearer", "eyj")
_COUNTERS = ("input_tokens", "output_tokens", "total_tokens",
             "prompt_tokens", "completion_tokens")
_DETAILS = {
    "input_tokens_details": ("cached_tokens",),
    "output_tokens_details": ("reasoning_tokens",),
    "prompt_tokens_details": ("cached_tokens",),
    "completion_tokens_details": (
        "reasoning_tokens", "accepted_prediction_tokens", "rejected_prediction_tokens",
    ),
}


class KnowledgeToolProtocolError(ValueError):
    """Only fixed, non-sensitive codes cross the parser/runtime boundary."""

    def __init__(self, code: str = _INVALID):
        self.code = code if type(code) is str and code in _ERROR_CODES else _INVALID
        super().__init__(self.code)


def tool_schema() -> dict:
    """Return a fresh OpenAI function schema with no model-supplied authority."""
    return {
        "type": "function",
        "function": {
            "name": TOOL_NAME,
            "description": (
                "Read local knowledge requested by this confirmed user turn, "
                "within its existing scope."
            ),
            "parameters": {
                "type": "object", "properties": {}, "required": [],
                "additionalProperties": False,
            },
        },
    }


def _fields(value: object, allowed: set[str]) -> dict:
    # A closed structural vocabulary prevents alternate/legacy call fields
    # from being silently treated as a successful text response.
    if type(value) is not dict or value.keys() - allowed:
        raise KnowledgeToolProtocolError()
    return value


def _identifier(value: object) -> str:
    if (type(value) is not str or not _ID.fullmatch(value)
            or value.lower().startswith(_SECRET_PREFIXES)):
        raise KnowledgeToolProtocolError()
    return value


def _model(value: object) -> str:
    if (type(value) is not str or not _MODEL.fullmatch(value)
            or "://" in value
            or any(part in {"", ".", ".."} for part in value.split("/"))
            or value.lower().startswith((*_SECRET_PREFIXES, "http:", "https:", "file:", "data:"))):
        raise KnowledgeToolProtocolError()
    return value


def _text(value: object, limit: int = MAX_RESPONSE_BYTES) -> str:
    if type(value) is not str or len(value) > limit:
        raise KnowledgeToolProtocolError()
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError:
        raise KnowledgeToolProtocolError() from None
    if size > limit:
        raise KnowledgeToolProtocolError()
    return value


def _count(value: object) -> bool:
    return type(value) is int and 0 <= value <= _MAX_COUNTER


def _usage(raw: object) -> dict:
    if type(raw) is not dict:
        return {}
    result = {key: raw[key] for key in _COUNTERS if _count(raw.get(key))}
    for group, keys in _DETAILS.items():
        source = raw.get(group)
        if type(source) is dict:
            values = {key: source[key] for key in keys if _count(source.get(key))}
            if values:
                result[group] = values
    return result


def _object_without_duplicates(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise KnowledgeToolProtocolError()
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise KnowledgeToolProtocolError()


def _empty_arguments(raw: object) -> None:
    raw = _text(raw, _MAX_ARGUMENT_BYTES)
    try:
        decoded = json.loads(raw, object_pairs_hook=_object_without_duplicates,
                             parse_constant=_reject_constant)
    except (ValueError, RecursionError):
        raise KnowledgeToolProtocolError() from None
    if type(decoded) is not dict or decoded:
        raise KnowledgeToolProtocolError()


def parse_completion(payload: object, expected_model: str, *, allow_tool: bool = True) -> dict:
    """Validate exactly one terminal assistant choice, without executing it.

    Final text/prefaces are bounded UTF-8 strings preserved for the caller's
    existing cleaner. A tool preface is private protocol history, never a final
    answer. Empty/null tool_calls on a final are harmless; any legacy field,
    extra executable metadata or second tool request is rejected. The small
    set of recognized non-executable metadata is discarded, never forwarded.
    Unknown structural fields fail closed. Usage is optional observed integer
    token counts only; invalid/unknown counters are omitted, never inferred.
    """
    if type(allow_tool) is not bool:
        raise KnowledgeToolProtocolError()
    expected_model = _model(expected_model)
    body = _fields(payload, {
        "id", "model", "choices", "usage", "object", "created",
        "system_fingerprint", "service_tier",
    })
    response_id = _identifier(body.get("id"))
    model = _model(body.get("model"))
    if model != expected_model:
        raise KnowledgeToolProtocolError()
    if "object" in body and body["object"] != "chat.completion":
        raise KnowledgeToolProtocolError()
    if "created" in body and not _count(body["created"]):
        raise KnowledgeToolProtocolError()
    for field in ("system_fingerprint", "service_tier"):
        if body.get(field) is not None:
            _text(body[field], 128)
    choices = body.get("choices")
    if type(choices) is not list or len(choices) != 1:
        raise KnowledgeToolProtocolError()
    choice = _fields(choices[0], {"index", "message", "finish_reason", "logprobs"})
    if type(choice.get("index")) is not int or choice["index"] != 0:
        raise KnowledgeToolProtocolError()
    if choice.get("logprobs") is not None and type(choice["logprobs"]) is not dict:
        raise KnowledgeToolProtocolError()
    message = _fields(choice.get("message"), {
        "role", "content", "tool_calls", "reasoning_content", "refusal",
    })
    if message.get("role") != "assistant":
        raise KnowledgeToolProtocolError()
    finish = choice.get("finish_reason")
    if finish == "length":
        raise KnowledgeToolProtocolError(_TRUNCATED)
    if type(finish) is not str or finish not in {"stop", "tool_calls"}:
        raise KnowledgeToolProtocolError(_INCOMPLETE)
    for field in ("reasoning_content", "refusal"):
        if message.get(field) is not None:
            _text(message[field])
    content = message.get("content")
    if content is not None:
        _text(content)
    result = {
        "response_id": response_id, "model": model, "usage": _usage(body.get("usage")),
    }
    calls = message.get("tool_calls")
    if finish == "stop":
        if (calls is not None and not (type(calls) is list and not calls)
                or content is None or not content.strip()):
            raise KnowledgeToolProtocolError()
        return {**result, "kind": "final", "text": content,
                "message": {"role": "assistant", "content": content}}
    if not allow_tool or type(calls) is not list or len(calls) != 1:
        raise KnowledgeToolProtocolError()
    call = _fields(calls[0], {"id", "type", "function"})
    call_id = _identifier(call.get("id"))
    if call.get("type") != "function":
        raise KnowledgeToolProtocolError()
    function = _fields(call.get("function"), {"name", "arguments"})
    if function.get("name") != TOOL_NAME:
        raise KnowledgeToolProtocolError()
    _empty_arguments(function.get("arguments"))
    normalized = {
        "role": "assistant", "content": content,
        "tool_calls": [{"id": call_id, "type": "function", "function": {
            "name": TOOL_NAME, "arguments": "{}",
        }}],
    }
    return {**result, "kind": "tool", "text": content if content is not None else "",
            "call_id": call_id, "message": normalized}
