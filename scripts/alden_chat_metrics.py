"""Sanitize per-turn generation evidence for Alden's durable chat ledger.

Integration order: reserve the typed turn, snapshot the selected route, construct
the adapter, invoke it with the reserved turn context, and then sanitize the
adapter's last_metrics. On errors/cancellation the caller must retain the
adapter reference and read last_metrics in a finally block before recording
the failed turn. This pure helper does not read settings or write the database.

Only observed values are emitted. In particular, the selected model never
serves as an observed response model, and an unscoped legacy ttft_ms is ignored:
older non-stream JSON responses mislabeled their completion time as TTFT.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from alden_model_routes import EFFORTS

MAX_GENERATION_JSON_BYTES = 4096
_MAX_COUNTER = 9_007_199_254_740_991
_MAX_TIME_MS = 86_400_000  # Guard against contaminated or wrong-unit metrics.
_ID = re.compile(r"[0-9a-f]{32}\Z")
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+:/-]{0,127}\Z")
_RESPONSE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_TRANSPORTS = frozenset({"opencodex", "local"})
# The route owns the vocabulary; its live catalog still checks each model's
# supported levels. Recording a request does not attest backend reasoning.
_EFFORTS = frozenset(EFFORTS)
_STATES = frozenset({"selecting_model", "running", "completed", "failed", "cancelled", "superseded"})
_STAGES = frozenset({"selection", "context", "input", "transport", "response", "completed"})
_ERROR_CODES = frozenset({
    "cancelled", "routed_model_not_ready", "routed_model_mismatch",
    "routed_llm_prompt_budget", "routed_llm_response_budget",
    "routed_llm_invalid_response", "routed_llm_generation_failed",
    "routed_llm_incomplete_response", "routed_llm_reply_truncated", "routed_llm_quota",
    "routed_llm_authentication", "routed_llm_unavailable",
    "model_effort_not_supported", "image_input_unavailable",
    "image_input_budget", "image_input_provenance_changed",
    "local_llm_request_failed", "local_llm_model_mismatch",
    "local_llm_response_invalid", "local_llm_response_too_large",
    "local_llm_stream_incomplete", "local_llm_reply_truncated",
    "local_llm_reply_incomplete", "local_llm_tool_call_unsupported",
    "local_llm_reply_empty", "local_llm_model_not_ready",
    "local_model_not_ready",
    "local_llm_tool_protocol_invalid", "local_llm_incomplete_response",
    "local_llm_prompt_budget", "local_knowledge_deadline_exceeded",
    "local_knowledge_input_changed", "local_knowledge_not_requested",
    "local_knowledge_source_changed", "local_knowledge_source_unavailable",
    "local_knowledge_result_too_large",
})
_USAGE_FIELDS = frozenset({
    "input_tokens", "output_tokens", "total_tokens",
    "prompt_tokens", "completion_tokens",
})
_USAGE_DETAIL_FIELDS = {
    "input_tokens_details": frozenset({"cached_tokens"}),
    "output_tokens_details": frozenset({"reasoning_tokens"}),
    "prompt_tokens_details": frozenset({"cached_tokens"}),
    "completion_tokens_details": frozenset({
        "reasoning_tokens", "accepted_prediction_tokens", "rejected_prediction_tokens",
    }),
}


def _invalid() -> ValueError:
    # Constant error: never reflect a credential, URL, or prompt from metadata.
    return ValueError("generation_metadata_invalid")


def _safe_model(value: object) -> str | None:
    if not isinstance(value, str) or not _MODEL.fullmatch(value):
        return None
    if ("://" in value or any(segment in {"", ".", ".."} for segment in value.split("/"))
            or value.lower().startswith((
                "http:", "https:", "file:", "data:", "sk-", "ghp_",
                "github_pat_", "xox",
            ))):
        return None
    return value


def _safe_response_id(value: object) -> str | None:
    if not isinstance(value, str) or not _RESPONSE_ID.fullmatch(value):
        return None
    if value.lower().startswith(("sk-", "ghp_", "github_pat_", "xox", "bearer", "eyj")):
        return None
    return value


def _count(value: object) -> int | None:
    return value if type(value) is int and 0 <= value <= _MAX_COUNTER else None


def _ms(value: object, multiplier: int = 1) -> float | None:
    if type(value) not in (int, float):
        return None
    if (value < 0 or value > _MAX_TIME_MS / multiplier
            or not math.isfinite(value)):
        return None
    return round(value * multiplier, 3)


def _one_of(value: object, allowed: frozenset[str]) -> bool:
    return isinstance(value, str) and value in allowed


def generation_error_code(value: object) -> str | None:
    """Share exact, non-sensitive generation reasons with the input receipt."""
    return value if _one_of(value, _ERROR_CODES) else None


def _usage(raw: object) -> dict:
    if not isinstance(raw, Mapping):
        return {}
    result = {}
    for key in _USAGE_FIELDS:
        value = _count(raw.get(key))
        if value is not None:
            result[key] = value
    for group, keys in _USAGE_DETAIL_FIELDS.items():
        source = raw.get(group)
        if not isinstance(source, Mapping):
            continue
        details = {}
        for key in keys:
            value = _count(source.get(key))
            if value is not None:
                details[key] = value
        if details:
            result[group] = details
    return result


def _tool_loop(raw):
    if not isinstance(raw, Mapping) or raw.get('state') not in {'no_call', 'read_completed', 'failed'}:
        raise _invalid()
    hops, reads = raw.get('hops'), raw.get('read_count')
    if not isinstance(hops, list) or len(hops) > 2 or type(reads) is not int or reads not in (0, 1):
        raise _invalid()
    safe = []
    for row in hops:
        if not isinstance(row, Mapping): raise _invalid()
        identifier, model, kind = _safe_response_id(row.get('id')), _safe_model(row.get('model')), row.get('kind')
        if identifier is None or model is None or kind not in {'tool', 'final'}: raise _invalid()
        safe.append({'id': identifier, 'model_id': model, 'kind': kind, 'usage': _usage(row.get('usage'))})
    if len({r['id'] for r in safe}) != len(safe): raise _invalid()
    kinds = [r['kind'] for r in safe]
    if reads and (not kinds or kinds[0] != 'tool'): raise _invalid()
    if 'read' in raw and reads != 1: raise _invalid()
    if raw['state'] == 'no_call' and (kinds != ['final'] or reads): raise _invalid()
    if raw['state'] == 'read_completed' and (kinds != ['tool', 'final'] or reads != 1): raise _invalid()
    if len(kinds) == 2 and (kinds != ['tool', 'final'] or reads != 1): raise _invalid()
    result = {'state': raw['state'], 'hop_count': len(safe), 'read_count': reads, 'hops': safe}
    elapsed = _ms(raw.get('elapsed_ms'))
    if elapsed is not None:
        result.update(elapsed_ms=elapsed, elapsed_scope='whole_local_knowledge_loop')
    if 'read' in raw:
        row = raw['read']
        if not isinstance(row, Mapping) or row.get('state') not in {'found','empty','ambiguous_room','not_requested','budget_exceeded'}:
            raise _invalid()
        mode, sources = row.get('mode'), _count(row.get('sources'))
        if not isinstance(mode, str) or not re.fullmatch('[a-z0-9_]{1,32}', mode) or sources is None:
            raise _invalid()
        result['read'] = {'state': row['state'], 'mode': mode, 'sources': sources}
    return result


def sanitize_generation(selection: object, metrics: object, ticket: object) -> dict:
    """Return JSON-safe evidence of at most 4 KiB, with no inferred observations.

    Top-level schema: schema_version (1), selection, request, response (when
    observed), timing (when measured), error_code (allowlisted only).
    The adapter context is emitted only when all three identity fields match
    the admitted ticket, otherwise it is marked unmeasured or rejected.
    """
    if not isinstance(ticket, Mapping):
        raise _invalid()
    expected = {key: ticket.get(key) for key in (
        "conversation_id", "turn_id", "context_version",
    )}
    if (not isinstance(expected["conversation_id"], str)
            or not _ID.fullmatch(expected["conversation_id"])
            or any(type(expected[key]) is not int or not 1 <= expected[key] <= _MAX_COUNTER
                   for key in ("turn_id", "context_version"))):
        raise _invalid()
    if selection is None:
        selection = {}
    if metrics is None:
        metrics = {}
    if not isinstance(selection, Mapping) or not isinstance(metrics, Mapping):
        raise _invalid()

    chosen = {}
    if "transport" in selection:
        if not _one_of(selection["transport"], _TRANSPORTS):
            raise _invalid()
        chosen["transport"] = selection["transport"]
    if "model" in selection:
        model = _safe_model(selection["model"])
        if model is None:
            raise _invalid()
        chosen["model_id"] = model
    if "reasoning_effort" in selection:
        effort = selection["reasoning_effort"]
        if effort is not None and not _one_of(effort, _EFFORTS):
            raise _invalid()
        chosen["reasoning_effort"] = effort

    raw_request = metrics.get("request")
    if raw_request is not None and not isinstance(raw_request, Mapping):
        raise _invalid()
    raw_request = raw_request or {}
    if (isinstance(raw_request.get('source'),str) and raw_request['source'] != ticket.get('source', 'text')):
        raise _invalid()
    if (_one_of(raw_request.get('transport'),_TRANSPORTS) and 'transport' in chosen
            and raw_request['transport'] != chosen['transport']):
        raise _invalid()
    if (_safe_model(raw_request.get('model_id')) is not None and 'model_id' in chosen
            and raw_request['model_id'] != chosen['model_id']):
        raise _invalid()
    request = {"context_binding": "unmeasured"}
    identity_keys = tuple(expected)
    if any(key in raw_request for key in identity_keys):
        if (not all(key in raw_request for key in identity_keys)
                or any(type(raw_request[key]) is not type(expected[key])
                       or raw_request[key] != expected[key] for key in identity_keys)):
            raise _invalid()
        request.update(expected)
        request["context_binding"] = "verified"
    transport = raw_request.get("transport")
    if _one_of(transport, _TRANSPORTS):
        request["transport"] = transport
    model = _safe_model(raw_request.get("model_id"))
    if model is not None:
        request["model_id"] = model
    effort = raw_request.get("reasoning_effort")
    if _one_of(effort, _EFFORTS):
        request["reasoning_effort"] = effort
    elif "reasoning_effort" in raw_request and effort is None:
        request["reasoning_effort"] = None
    if _one_of(raw_request.get("state"), _STATES):
        request["state"] = raw_request["state"]
    if type(raw_request.get("cancelled")) is bool:
        request["cancelled"] = raw_request["cancelled"]
    if _one_of(metrics.get("stage"), _STAGES):
        request["stage"] = metrics["stage"]

    result = {"schema_version": 1, "selection": chosen, "request": request}
    response = {}
    observed_model = _safe_model(metrics.get("response_model_id"))
    if observed_model is not None:
        response["model_id"] = observed_model
    backend_id = _safe_response_id(metrics.get("backend_request_id"))
    if backend_id is not None:
        response["id"] = backend_id
    counts = _usage(metrics.get("usage"))
    if counts:
        response["usage"] = counts
    if response:
        result["response"] = response

    timing = {}
    elapsed = _ms(metrics.get("elapsed_ms"))
    if elapsed is not None:
        timing.update(elapsed_ms=elapsed, elapsed_scope="routed_generation_call")
    elif chosen.get('transport') == 'local':
        elapsed = _ms(metrics.get("elapsed_seconds"), 1000)
        if elapsed is not None:
            timing.update(elapsed_ms=elapsed, elapsed_scope="local_call_to_stream_end")
    first = None
    scope = None
    if metrics.get("first_output_scope") == "stream_output_text_delta":
        first = _ms(metrics.get("first_output_ms"))
        scope = "stream_output_text_delta"
    elif chosen.get("transport") == "local":
        first = _ms(metrics.get("first_visible_token_seconds"), 1000)
        scope = "local_stream_visible_content"
    if first is not None and (elapsed is None or first <= elapsed):
        timing.update(first_output_ms=first, first_output_scope=scope)
    if timing:
        result["timing"] = timing
    if 'tool_loop' in metrics:
        result['tool_loop'] = _tool_loop(metrics['tool_loop'])

    error_code = generation_error_code(metrics.get("error_code"))
    if error_code is not None:
        result["error_code"] = error_code
    if len(json.dumps(result, ensure_ascii=True, allow_nan=False,
                      sort_keys=True, separators=(",", ":")).encode("utf-8")) > MAX_GENERATION_JSON_BYTES:
        raise _invalid()
    return result
