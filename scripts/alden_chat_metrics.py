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
from alden_knowledge_tool_protocol import MAX_RESPONSE_BYTES

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
_WIRE_COUNT_LIMIT = 64
_WIRE_OUTER_STATES = frozenset({'object', 'invalid_type'})
_WIRE_FINISH_REASONS = frozenset({'stop', 'tool_calls', 'length', 'content_filter', 'function_call'})
_WIRE_ENUMS = {
    'choices_state': frozenset({'missing', 'invalid_type', 'list'}),
    'first_choice_state': frozenset({'object', 'invalid_type'}),
    'finish_reason': _WIRE_FINISH_REASONS | {'missing', 'null', 'invalid_type', 'unknown'},
    'message_state': frozenset({'missing', 'invalid_type', 'object'}),
    'tool_calls_state': frozenset({'missing', 'null', 'invalid_type', 'list'}),
}
_WIRE_BOOLEANS = frozenset({
    'choice_count_overflow', 'choice_index_valid', 'tool_call_count_overflow',
    'legacy_function_call_present',
})
_WIRE_COUNTS = frozenset({'choice_count', 'tool_call_count'})
# The chat caller appends terminal_state after sanitizing. Keep room for the
# longest existing terminal label without increasing the durable 4 KiB bound.
_WIRE_TERMINAL_RESERVE = 32
_WIRE_OPTIONAL_SHAPES = (
    'first_choice_state', 'choice_index_valid', 'message_state',
    'legacy_function_call_present', 'choices_state', 'tool_calls_state',
)


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


def observe_local_knowledge_completion(payload, *, response_bytes, allow_tool):
    """Describe decoded HTTP shape before acceptance, without private values.

    A wire finish reason does not attest the decoder's original termination or
    tool spans that a serving formatter may have removed. Missing structures
    remain missing observations; only actual lists have observed counts.
    """
    if (type(response_bytes) is not int or not 0 <= response_bytes <= MAX_RESPONSE_BYTES
            or type(allow_tool) is not bool):
        raise _invalid()
    result = {'origin': 'local_http_completion', 'response_bytes': response_bytes,
              'allow_tool': allow_tool,
              'outer_state': 'object' if type(payload) is dict else 'invalid_type'}
    if type(payload) is not dict:
        return result
    if 'choices' not in payload:
        result['choices_state'] = 'missing'
        return result
    choices = payload['choices']
    if type(choices) is not list:
        result['choices_state'] = 'invalid_type'
        return result
    result.update(choices_state='list', choice_count=min(len(choices), _WIRE_COUNT_LIMIT),
                  choice_count_overflow=len(choices) > _WIRE_COUNT_LIMIT)
    if not choices:
        return result
    choice = choices[0]
    result['first_choice_state'] = 'object' if type(choice) is dict else 'invalid_type'
    if type(choice) is not dict:
        return result
    result['choice_index_valid'] = type(choice.get('index')) is int and choice['index'] == 0
    reason = choice.get('finish_reason')
    if 'finish_reason' not in choice:
        result['finish_reason'] = 'missing'
    elif reason is None:
        result['finish_reason'] = 'null'
    elif type(reason) is not str:
        result['finish_reason'] = 'invalid_type'
    else:
        result['finish_reason'] = reason if reason in _WIRE_FINISH_REASONS else 'unknown'
    if 'message' not in choice:
        result['message_state'] = 'missing'
        return result
    message = choice['message']
    result['message_state'] = 'object' if type(message) is dict else 'invalid_type'
    if type(message) is not dict:
        return result
    result['legacy_function_call_present'] = 'function_call' in message
    if 'tool_calls' not in message:
        result['tool_calls_state'] = 'missing'
    elif message['tool_calls'] is None:
        result['tool_calls_state'] = 'null'
    elif type(message['tool_calls']) is not list:
        result['tool_calls_state'] = 'invalid_type'
    else:
        calls = message['tool_calls']
        result.update(tool_calls_state='list', tool_call_count=min(len(calls), _WIRE_COUNT_LIMIT),
                      tool_call_count_overflow=len(calls) > _WIRE_COUNT_LIMIT)
    return result


def _wire_observations(raw):
    if not isinstance(raw, list) or len(raw) > 2:
        raise _invalid()
    result = []
    for row in raw:
        if (not isinstance(row, Mapping) or row.get('origin') != 'local_http_completion'
                or type(row.get('response_bytes')) is not int
                or not 0 <= row['response_bytes'] <= MAX_RESPONSE_BYTES
                or type(row.get('allow_tool')) is not bool
                or not _one_of(row.get('outer_state'), _WIRE_OUTER_STATES)):
            raise _invalid()
        safe = {key: row[key] for key in ('origin', 'response_bytes', 'allow_tool', 'outer_state')}
        for key, allowed in _WIRE_ENUMS.items():
            if _one_of(row.get(key), allowed):
                safe[key] = row[key]
        for key in _WIRE_BOOLEANS:
            if type(row.get(key)) is bool:
                safe[key] = row[key]
        for key in _WIRE_COUNTS:
            value = row.get(key)
            if type(value) is int and 0 <= value <= _WIRE_COUNT_LIMIT:
                safe[key] = value
        result.append(safe)
    return result


def _generation_size(value):
    return len(json.dumps(value, ensure_ascii=True, allow_nan=False,
                          sort_keys=True, separators=(',', ':')).encode('utf-8'))


def _fit_wire_observations(result, *, reserve=_WIRE_TERMINAL_RESERVE):
    loop = result.get('tool_loop', {})
    observations = loop.get('wire_observations')
    if not observations:
        if (loop.get('wire_observations_omitted_for_budget') is True
                and _generation_size(result) > MAX_GENERATION_JSON_BYTES - reserve):
            loop.pop('wire_observations_omitted_for_budget')
        return
    target = MAX_GENERATION_JSON_BYTES - reserve
    if _generation_size(result) <= target:
        return
    # Only diagnostic shape fields are optional here. Keep request/source
    # binding, usage, finish reasons, observed counts/overflow and core bases.
    # A missing compacted field is unknown, never an inferred zero or success.
    for key in _WIRE_OPTIONAL_SHAPES:
        for row in observations:
            if key == 'legacy_function_call_present' and row.get(key) is True:
                continue
            if key in row:
                del row[key]
                loop['wire_observations_compacted'] = True
        if _generation_size(result) <= target:
            return
    # These new optional diagnostics must not invalidate previously valid
    # primary evidence. Prefer the latest response if a large attachment/source
    # receipt leaves no room for both. An omitted row is unknown, not zero.
    while observations and _generation_size(result) > target:
        observations.pop(0)
        loop['wire_observations_omitted_for_budget'] = True
    if not observations:
        del loop['wire_observations']
        loop.pop('wire_observations_compacted', None)
    if _generation_size(result) > target:
        loop.pop('wire_observations_omitted_for_budget', None)


def bound_generation_evidence(result, *, reserve=0):
    """Fit optional diagnostics after the host adds safe receipt fields.

    Primary evidence must already be sanitized by its owner. This does not
    sanitize arbitrary additional fields or relax the durable byte limit.
    """
    _fit_wire_observations(result, reserve=reserve)
    if _generation_size(result) > MAX_GENERATION_JSON_BYTES:
        raise _invalid()
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
    if 'wire_observations' in raw:
        result['wire_observations'] = _wire_observations(raw['wire_observations'])
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
    retrieval = metrics.get('retrieval')
    if isinstance(retrieval, Mapping) and 'query_trace' in retrieval:
        from alden_scoped_knowledge_query import POLICY
        trace = retrieval['query_trace']
        fields = {'policy', 'derived', 'request_sha256', 'retrieval_sha256'}
        if (not isinstance(trace, Mapping) or set(trace) != fields
                or trace.get('policy') != POLICY or type(trace.get('derived')) is not bool
                or any(not isinstance(trace.get(key), str)
                       or re.fullmatch('[0-9a-f]{64}', trace[key]) is None
                       for key in ('request_sha256', 'retrieval_sha256'))
                or trace['derived'] != (trace['request_sha256'] != trace['retrieval_sha256'])):
            raise _invalid()
        result['knowledge_query'] = {key: trace[key] for key in sorted(fields)}

    error_code = generation_error_code(metrics.get("error_code"))
    if error_code is not None:
        result["error_code"] = error_code
    return bound_generation_evidence(result, reserve=_WIRE_TERMINAL_RESERVE)
