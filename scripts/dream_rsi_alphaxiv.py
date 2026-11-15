#!/usr/bin/env python3
"""Fail-closed alphaXiv paper provenance for the offline DREAM-RSI loop.

The existing DREAM-RSI simulator and fine-tune module keep their public
behaviour.  This module only composes three kinds of provenance:

* paper evidence returned by the ``alphaxiv`` CLI,
* fixed-budget DREAM-RSI candidate/branch/stop history, and
* DPO preference evaluation from response-token log probabilities.

It never promotes or replaces a live model.  It also never turns DREAM-RSI's
character-similarity replay score into paper evidence.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

try:  # scripts.* imports in tests; sibling imports when run as a flat script.
    from scripts.auto_reply_dream_rsi import run_fixed_budget_loop
    from scripts.auto_reply_finetune import (
        DPO_EVAL_UNAVAILABLE,
        evaluate_preference_pairs,
    )
except ImportError:  # pragma: no cover - flat script execution
    from auto_reply_dream_rsi import run_fixed_budget_loop
    from auto_reply_finetune import DPO_EVAL_UNAVAILABLE, evaluate_preference_pairs


REPORT_SCHEMA_VERSION = 1
DEFAULT_QUERY = "DREAM-RSI recursive self-improvement dreaming"
DREAM_RSI_PAPER_ID = "2609.14858"
DEFAULT_TIMEOUT_SECONDS = 10.0
MAX_TIMEOUT_SECONDS = 60.0
MAX_QUERY_CHARS = 512
MAX_CLI_JSON_BYTES = 512 * 1024
MAX_INPUT_JSON_BYTES = 256 * 1024
MAX_REPORT_JSON_BYTES = 512 * 1024
MAX_SUMMARY_CHARS = 64 * 1024
MAX_CANDIDATES = 32
MAX_DPO_PAIRS = 128
MAX_RUNTIME_MODELS = 16

_ARXIV_ID_RE = re.compile(
    r"^(?:\d{4}\.\d{4,5}|[A-Za-z][A-Za-z0-9._-]*/\d{7})(?:v\d+)?$"
)
_PAPER_CONTAINER_KEYS = ("papers", "results", "items", "data")


def _valid_plain_text(value: Any, *, limit: int, allow_empty: bool = False) -> str:
    text = str(value or "").strip()
    if not text and not allow_empty:
        raise ValueError("empty_text")
    if len(text) > limit:
        raise ValueError("text_too_long")
    if any(ord(char) < 32 for char in text):
        raise ValueError("control_character")
    return text


def _validate_query(query: Any) -> str:
    text = _valid_plain_text(query, limit=MAX_QUERY_CHARS)
    # A leading option token could be reinterpreted by a third-party CLI even
    # though subprocess is invoked without a shell.
    if text.startswith("-"):
        raise ValueError("query_looks_like_option")
    return text


def _validate_paper_id(value: Any) -> str:
    paper_id = _valid_plain_text(value, limit=96)
    if ".." in paper_id or not _ARXIV_ID_RE.fullmatch(paper_id):
        raise ValueError("invalid_paper_id")
    return paper_id


def _resolve_cli(requested: Any) -> tuple[str | None, str | None]:
    """Resolve one executable without allowing option/path injection."""
    try:
        value = _valid_plain_text(requested, limit=4096)
    except ValueError:
        return None, "invalid_cli_path"

    candidate = Path(value).expanduser()
    if candidate.is_absolute():
        if ".." in Path(value).parts:
            return None, "invalid_cli_path"
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            return None, "cli_missing"
        if not resolved.is_file() or not os.access(resolved, os.X_OK):
            return None, "cli_missing"
        return str(resolved), None

    # Relative paths are deliberately rejected.  A bare executable name is
    # resolved through PATH; embedded separators would otherwise permit ../.
    if "/" in value or "\\" in value or value in {".", ".."}:
        return None, "invalid_cli_path"
    found = shutil.which(value)
    if not found:
        return None, "cli_missing"
    return found, None


def _bytes(value: Any) -> bytes:
    if value is None:
        return b""
    if isinstance(value, bytes):
        return value
    return str(value).encode("utf-8", errors="replace")


def _decode_small(value: Any, *, limit: int) -> tuple[str | None, str | None]:
    raw = _bytes(value)
    if len(raw) > limit:
        return None, "output_too_large"
    try:
        return raw.decode("utf-8"), None
    except UnicodeDecodeError:
        return None, "invalid_utf8"


def _sanitize_stderr(value: Any) -> str:
    """Return a bounded stderr preview safe for provenance serialization."""
    try:
        if isinstance(value, bytes):
            text = value.decode("utf-8", errors="replace")
        else:
            text = "" if value is None else str(value)
        sanitized = []
        for character in text:
            category = unicodedata.category(character)
            if category == "Cc":
                sanitized.append(" ")
            elif not category.startswith("C"):
                sanitized.append(character)
        text = "".join(sanitized)
        text = re.sub(r"\s+", " ", text).strip()
        text = re.sub(r"[A-Za-z0-9_-]{32,}", "<redacted>", text)
        return text[:200]
    except Exception:
        return ""


def _run_cli(
    executable: str,
    args: Sequence[str],
    *,
    timeout: float,
    env: dict[str, str],
    expect_json: bool,
    operation: str,
) -> tuple[Any | None, dict[str, Any]]:
    command = [executable, *[str(arg) for arg in args]]
    record: dict[str, Any] = {
        "operation": operation,
        "argv": list(args),
        "status": "unavailable",
        "returncode": None,
    }
    try:
        completed = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
            shell=False,
            env=env,
        )
    except subprocess.TimeoutExpired:
        record["reason"] = "cli_timeout"
        return None, record
    except OSError as exc:
        record["reason"] = f"cli_exec_error:{type(exc).__name__}"
        return None, record

    record["returncode"] = int(completed.returncode)
    stderr_text, _ = _decode_small(completed.stderr, limit=4096)
    if stderr_text:
        record["stderr"] = _sanitize_stderr(stderr_text)
    if completed.returncode != 0:
        record["reason"] = "cli_nonzero"
        return None, record

    stdout_text, decode_error = _decode_small(
        completed.stdout, limit=MAX_CLI_JSON_BYTES
    )
    if decode_error:
        record["reason"] = decode_error
        return None, record
    if not expect_json:
        record["status"] = "ok"
        return stdout_text or "", record

    try:
        payload = json.loads(stdout_text or "")
    except (json.JSONDecodeError, ValueError):
        record["reason"] = "invalid_json"
        return None, record
    if not isinstance(payload, (dict, list)):
        record["reason"] = "invalid_json_shape"
        return None, record
    record["status"] = "ok"
    return payload, record


def _iter_paper_nodes(payload: Any):
    if isinstance(payload, list):
        for item in payload:
            yield from _iter_paper_nodes(item)
        return
    if not isinstance(payload, dict):
        return

    if any(key in payload for key in ("paper_id", "paperId", "arxiv_id", "arxivId")):
        yield payload
    for key in _PAPER_CONTAINER_KEYS:
        child = payload.get(key)
        if isinstance(child, (dict, list)):
            yield from _iter_paper_nodes(child)


def _paper_id_from_node(node: dict[str, Any]) -> str | None:
    for key in ("paper_id", "paperId", "arxiv_id", "arxivId", "id"):
        value = node.get(key)
        if value is None:
            continue
        try:
            return _validate_paper_id(value)
        except ValueError:
            continue
    for key in ("url", "paper_url", "arxiv_url"):
        value = str(node.get(key) or "")
        match = re.search(
            r"/(?:abs|pdf)/((?:\d{4}\.\d{4,5}|[A-Za-z][A-Za-z0-9._-]*/\d{7})(?:v\d+)?)",
            value,
        )
        if match:
            try:
                return _validate_paper_id(match.group(1))
            except ValueError:
                pass
    return None


def _first_direct_text(node: Any, keys: Sequence[str], *, limit: int) -> str:
    if not isinstance(node, dict):
        return ""
    for key in keys:
        value = node.get(key)
        if isinstance(value, str):
            text = value.strip()
            if 0 < len(text) <= limit:
                return text
    return ""


def _extract_search_papers(payload: Any) -> list[dict[str, str]]:
    papers: list[dict[str, str]] = []
    seen: set[str] = set()
    for node in _iter_paper_nodes(payload):
        paper_id = _paper_id_from_node(node)
        title = _first_direct_text(node, ("title", "paper_title", "name"), limit=1000)
        if not paper_id or not title or paper_id in seen:
            continue
        url = _first_direct_text(node, ("url", "paper_url", "arxiv_url"), limit=2048)
        papers.append({"paper_id": paper_id, "title": title, "url": url})
        seen.add(paper_id)
        if len(papers) >= 20:
            break
    return papers


def _find_value(payload: Any, keys: set[str]) -> Any:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in keys and value not in (None, "", [], {}):
                return value
        for key in ("paper", "data", "result", "context", "summary"):
            child = payload.get(key)
            if isinstance(child, (dict, list)):
                found = _find_value(child, keys)
                if found not in (None, "", [], {}):
                    return found
    elif isinstance(payload, list):
        for item in payload:
            found = _find_value(item, keys)
            if found not in (None, "", [], {}):
                return found
    return None


def _context_identity(payload: Any) -> tuple[str | None, str]:
    raw_id = _find_value(payload, {"paper_id", "paperId", "arxiv_id", "arxivId"})
    paper_id: str | None = None
    if raw_id is not None:
        try:
            paper_id = _validate_paper_id(raw_id)
        except ValueError:
            paper_id = None
    raw_title = _find_value(payload, {"title", "paper_title"})
    title = ""
    if isinstance(raw_title, str):
        title = raw_title.strip()[:1000]
    return paper_id, title


def _summary_text(payload: Any) -> str | None:
    value = _find_value(payload, {"summary", "summary_text"})
    if isinstance(value, dict):
        value = _find_value(value, {"summary", "text", "content"})
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > MAX_SUMMARY_CHARS:
        return None
    return text


@dataclass
class _PaperAttempt:
    """What one provider attempt knows so far; every paper report is built from it."""

    provider: str
    isolated_context: bool
    requested_cli: str
    query: str
    resolved_cli: str | None = None
    selected_paper: dict[str, str] | None = None
    commands: list[dict[str, Any]] = field(default_factory=list)

    def unavailable(self, reason: str) -> dict[str, Any]:
        return self._report("unavailable", reason, selection_method="none", evidence=None)

    def verified(
        self, reason: str, *, selection_method: str, evidence: dict[str, str]
    ) -> dict[str, Any]:
        return self._report("ok", reason, selection_method=selection_method, evidence=evidence)

    def _report(
        self,
        status: str,
        reason: str,
        *,
        selection_method: str,
        evidence: dict[str, str] | None,
    ) -> dict[str, Any]:
        return {
            "status": status,
            "reason": reason,
            "analysis_available": evidence is not None,
            "provider": self.provider,
            "query": self.query,
            "selection_method": selection_method,
            "selected_paper": self.selected_paper,
            "evidence": evidence,
            "string_similarity_used": False,
            "cli": {
                "requested": self.requested_cli,
                "resolved": self.resolved_cli,
                "isolated_context": self.isolated_context,
                "commands": self.commands,
            },
        }


def _verified_orx_summary(report_text: str, paper_id: str) -> tuple[str | None, str]:
    """Return the report body, or the reason its alphaXiv header cannot be trusted."""
    lines = report_text.splitlines()
    header_index = next(
        (index for index, line in enumerate(lines) if line.strip()),
        None,
    )
    if header_index is None:
        return None, "incomplete_summary_response"
    identity = re.compile(
        rf"^alphaXiv:\s*https?://(?:www\.)?alphaxiv\.org/abs/{re.escape(paper_id)}(?:v\d+)?\s*$"
    )
    if not identity.fullmatch(lines[header_index].strip()):
        return None, "identity_mismatch"
    summary = "\n".join(lines[header_index + 1 :]).strip()
    if not summary:
        return None, "incomplete_summary_response"
    if len(summary) > MAX_SUMMARY_CHARS:
        return None, "text_too_long"
    return summary, ""


def collect_orx_paper_report(
    query: str = DEFAULT_QUERY,
    *,
    orx_cli: str = "orx",
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    paper_id: str = DREAM_RSI_PAPER_ID,
) -> dict[str, Any]:
    """Collect one identity-verified alphaXiv report through OpenResearch."""
    attempt = _PaperAttempt(
        provider="orx_cli",
        isolated_context=False,
        requested_cli=str(orx_cli),
        query=str(query)[:MAX_QUERY_CHARS],
    )
    try:
        attempt.query = _validate_query(query)
    except ValueError as exc:
        return attempt.unavailable(f"invalid_query:{exc}")
    try:
        selected_id = _validate_paper_id(paper_id)
    except ValueError:
        return attempt.unavailable("invalid_paper_id")
    try:
        timeout_value = float(timeout)
    except (OverflowError, TypeError, ValueError):
        timeout_value = float("nan")
    if not math.isfinite(timeout_value):
        return attempt.unavailable("invalid_timeout")
    timeout_value = min(MAX_TIMEOUT_SECONDS, max(0.1, timeout_value))

    executable, resolve_error = _resolve_cli(orx_cli)
    if resolve_error or not executable:
        return attempt.unavailable(resolve_error or "cli_missing")
    attempt.resolved_cli = executable

    report_text, command = _run_cli(
        executable,
        ("paper", selected_id, "--source", "alphaxiv", "--no-telemetry"),
        timeout=timeout_value,
        env=dict(os.environ),
        expect_json=False,
        operation="orx_paper",
    )
    attempt.commands.append(command)
    attempt.selected_paper = {
        "paper_id": selected_id,
        "title": "",
        "url": f"https://www.alphaxiv.org/abs/{selected_id}",
    }
    if report_text is None:
        return attempt.unavailable(str(command.get("reason") or "paper_unavailable"))

    summary, failure = _verified_orx_summary(report_text, selected_id)
    if summary is None:
        return attempt.unavailable(failure)
    return attempt.verified(
        "orx_report_verified",
        selection_method="direct_paper_id",
        evidence={
            "kind": "orx_alphaxiv_paper_report",
            "summary": summary,
            "source_operation": "orx_paper",
        },
    )


def collect_paper_report(
    query: str = DEFAULT_QUERY,
    *,
    alphaxiv_cli: str = "alphaxiv",
    orx_cli: str = "orx",
    paper_source: str = "alphaxiv",
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    paper_id: str | None = None,
) -> dict[str, Any]:
    """Collect deterministic paper evidence through one selected provider."""
    if paper_source == "auto":
        if _resolve_cli(alphaxiv_cli)[0]:
            paper_source = "alphaxiv"
        elif _resolve_cli(orx_cli)[0]:
            paper_source = "orx"
        else:
            return _PaperAttempt(
                provider="orx_cli",
                isolated_context=False,
                requested_cli=str(orx_cli),
                query=str(query)[:MAX_QUERY_CHARS],
            ).unavailable("cli_missing")
    if paper_source == "orx":
        return collect_orx_paper_report(
            query,
            orx_cli=orx_cli,
            timeout=timeout,
            paper_id=paper_id or DREAM_RSI_PAPER_ID,
        )
    if paper_source == "alphaxiv":
        return _collect_alphaxiv_paper_report(
            query, alphaxiv_cli=alphaxiv_cli, timeout=timeout, paper_id=paper_id
        )
    return _PaperAttempt(
        provider="alphaxiv_cli",
        isolated_context=True,
        requested_cli=str(alphaxiv_cli),
        query=str(query)[:MAX_QUERY_CHARS],
    ).unavailable("invalid_paper_source")


def _collect_alphaxiv_paper_report(
    query: str,
    *,
    alphaxiv_cli: str,
    timeout: float,
    paper_id: str | None,
) -> dict[str, Any]:
    """Select a paper, then verify its identity and summary in an isolated CLI home."""
    attempt = _PaperAttempt(
        provider="alphaxiv_cli",
        isolated_context=True,
        requested_cli=str(alphaxiv_cli),
        query=str(query)[:MAX_QUERY_CHARS],
    )
    try:
        safe_query = attempt.query = _validate_query(query)
    except ValueError as exc:
        return attempt.unavailable(f"invalid_query:{exc}")
    try:
        timeout_value = float(timeout)
    except (OverflowError, TypeError, ValueError):
        timeout_value = -1.0
    if not (0.1 <= timeout_value <= MAX_TIMEOUT_SECONDS):
        return attempt.unavailable("invalid_timeout")

    executable, resolve_error = _resolve_cli(alphaxiv_cli)
    if resolve_error or not executable:
        return attempt.unavailable(resolve_error or "cli_missing")
    attempt.resolved_cli = executable

    with tempfile.TemporaryDirectory(prefix="openkakao-alphaxiv-") as temp_home:
        env = dict(os.environ)
        env["ALPHAXIV_HOME"] = temp_home

        def run(operation: str, *args: str, expect_json: bool) -> tuple[Any | None, str]:
            payload, record = _run_cli(
                executable,
                args,
                timeout=timeout_value,
                env=env,
                expect_json=expect_json,
                operation=operation,
            )
            attempt.commands.append(record)
            return payload, str(record.get("reason") or "")

        if paper_id:
            try:
                selected_id = _validate_paper_id(paper_id)
            except ValueError:
                return attempt.unavailable("invalid_paper_id")
            selected = {"paper_id": selected_id, "title": "", "url": ""}
        else:
            search_payload, failure = run(
                "search_papers", "search", "papers", safe_query, "--json", expect_json=True
            )
            if search_payload is None:
                return attempt.unavailable(failure or "search_unavailable")
            papers = _extract_search_papers(search_payload)
            if not papers:
                return attempt.unavailable("incomplete_search_response")
            selected = papers[0]
        attempt.selected_paper = selected

        context_payload, failure = run(
            "context_use_paper", "context", "use", "paper", selected["paper_id"], expect_json=False
        )
        if context_payload is None:
            return attempt.unavailable(failure or "context_unavailable")

        shown, failure = run("context_show", "context", "show", "--json", expect_json=True)
        if shown is None:
            return attempt.unavailable(failure or "context_unavailable")
        context_id, context_title = _context_identity(shown)
        if context_id != selected["paper_id"]:
            return attempt.unavailable("paper_identity_mismatch")
        selected["title"] = selected["title"] or context_title
        if not selected["title"]:
            return attempt.unavailable("incomplete_paper_metadata")

        summary_payload, failure = run(
            "paper_summary", "paper", "summary", "--json", expect_json=True
        )
        if summary_payload is None:
            return attempt.unavailable(failure or "summary_unavailable")
        summary = _summary_text(summary_payload)
        if summary is None:
            return attempt.unavailable("incomplete_summary_response")

    return attempt.verified(
        "alphaxiv_summary_verified",
        selection_method="direct_paper_id" if paper_id else "alphaxiv_search_rank_1",
        evidence={
            "kind": "alphaxiv_paper_summary",
            "summary": summary,
            "source_operation": "paper_summary",
        },
    )


def _safe_eval_report(report: Any) -> dict[str, Any]:
    if not isinstance(report, dict):
        return {"status": "eval_unavailable", "reason": "invalid_candidate_evaluation"}
    result: dict[str, Any] = {
        "status": str(report.get("status") or "eval_unavailable")[:64],
    }
    for key in ("reason", "loss", "factuality", "self_talk", "tone", "grounding", "latency"):
        if key in report and isinstance(report[key], (str, int, float, bool, type(None))):
            result[key] = report[key]
        failed_key = f"{key}_failed"
        if failed_key in report and isinstance(report[failed_key], bool):
            result[failed_key] = report[failed_key]
    return result


def build_exploration_provenance(exploration: Any) -> dict[str, Any]:
    """Run the existing fixed-budget selector over bounded precomputed evals."""
    if not isinstance(exploration, dict):
        return {
            "status": "not_supplied",
            "fixed_budget": {"initial": 0, "consumed": 0, "remaining": 0},
            "candidate_order": [],
            "events": [],
            "stop": {"reason": "not_supplied"},
            "promotion": {"allowed": False, "observed": False},
        }
    budget = exploration.get("budget", 0)
    if isinstance(budget, bool) or not isinstance(budget, int) or not (0 <= budget <= MAX_CANDIDATES):
        return {"status": "invalid_input", "reason": "invalid_budget"}
    raw_candidates = exploration.get("candidates")
    if not isinstance(raw_candidates, list) or len(raw_candidates) > MAX_CANDIDATES:
        return {"status": "invalid_input", "reason": "invalid_candidates"}

    names: list[str] = []
    evaluations: dict[str, dict[str, Any]] = {}
    for raw in raw_candidates:
        if not isinstance(raw, dict):
            return {"status": "invalid_input", "reason": "invalid_candidate"}
        try:
            name = _valid_plain_text(raw.get("name"), limit=128)
        except ValueError:
            return {"status": "invalid_input", "reason": "invalid_candidate_name"}
        if name in evaluations:
            return {"status": "invalid_input", "reason": "duplicate_candidate"}
        names.append(name)
        evaluations[name] = _safe_eval_report(raw.get("evaluation"))

    def evaluate(name: str) -> dict[str, Any]:
        return dict(evaluations.get(name) or {"status": "eval_unavailable", "reason": "missing_candidate"})

    loop = run_fixed_budget_loop(candidates=names, evaluate_fn=evaluate, budget=budget)
    history = loop.get("history") if isinstance(loop.get("history"), list) else []
    events: list[dict[str, Any]] = []
    for index, item in enumerate(history, start=1):
        if not isinstance(item, dict):
            continue
        decision = item.get("decision") if isinstance(item.get("decision"), dict) else {}
        report = item.get("report") if isinstance(item.get("report"), dict) else {}
        events.append(
            {
                "step": index,
                "event": "candidate_evaluated",
                "candidate": str(item.get("candidate") or "")[:128],
                "selection": {
                    "action": str(decision.get("action") or "")[:64],
                    "reason": str(decision.get("reason") or "")[:128],
                },
                "evaluation": _safe_eval_report(report),
            }
        )
    stop_reason = str(loop.get("reason") or "")[:128]
    events.append(
        {
            "step": len(history) + 1,
            "event": "stop",
            "reason": stop_reason,
        }
    )
    consumed = len(history)
    promoted = bool(loop.get("promoted", False))
    return {
        "status": "policy_violation" if promoted else str(loop.get("status") or "stopped"),
        "fixed_budget": {
            "initial": budget,
            "consumed": consumed,
            "remaining": max(0, budget - consumed),
        },
        "candidate_order": names,
        "events": events,
        "stop": {"reason": stop_reason},
        "promotion": {"allowed": False, "observed": promoted},
    }


def build_dpo_provenance(dpo: Any) -> dict[str, Any]:
    """Evaluate preference pairs only from response-token log probabilities."""
    if not isinstance(dpo, dict):
        return {
            "status": DPO_EVAL_UNAVAILABLE,
            "reason": "not_supplied",
            "evaluated": 0,
            "unavailable": 0,
            "mean_loss": None,
            "method": "response_token_logprob",
            "string_similarity_used": False,
            "pairs": [],
        }

    def invalid(reason: str) -> dict[str, Any]:
        return {
            "status": "invalid_input",
            "reason": reason,
            "method": "response_token_logprob",
            "string_similarity_used": False,
            "pairs": [],
        }

    pairs = dpo.get("pairs")
    if not isinstance(pairs, list) or len(pairs) > MAX_DPO_PAIRS:
        return invalid("invalid_pairs")
    try:
        tokenizer_id = _valid_plain_text(dpo.get("tokenizer_id"), limit=256, allow_empty=True)
        base_model = _valid_plain_text(dpo.get("base_model"), limit=512, allow_empty=True)
        beta = float(dpo.get("beta", 0.1))
    except (ValueError, TypeError):
        return invalid("invalid_dpo_metadata")
    if not (0.0 < beta <= 10.0):
        return invalid("invalid_beta")
    if any(not isinstance(pair, dict) for pair in pairs):
        return invalid("invalid_pair")

    evaluated = evaluate_preference_pairs(
        pairs,
        tokenizer_id=tokenizer_id,
        base_model=base_model,
        beta=beta,
    )
    pair_reports: list[dict[str, Any]] = []
    for report in evaluated.get("pairs", []):
        if not isinstance(report, dict):
            continue
        pair_reports.append(
            {
                "pair_id": str(report.get("pair_id") or "")[:128],
                "source": str(report.get("source") or "")[:128],
                "status": str(report.get("status") or DPO_EVAL_UNAVAILABLE)[:64],
                "reason": str(report.get("reason") or "")[:128],
                "loss": report.get("loss") if isinstance(report.get("loss"), (int, float)) else None,
            }
        )
    result = {
        "status": str(evaluated.get("status") or DPO_EVAL_UNAVAILABLE),
        "evaluated": int(evaluated.get("evaluated") or 0),
        "unavailable": int(evaluated.get("unavailable") or 0),
        "mean_loss": evaluated.get("mean_loss"),
        "tokenizer_id": tokenizer_id,
        "base_model": base_model,
        "beta": beta,
        "method": "response_token_logprob",
        "string_similarity_used": False,
        "pairs": pair_reports,
    }
    if result["status"] == DPO_EVAL_UNAVAILABLE:
        result["reason"] = "missing_logprobs" if pairs else "no_pairs"
    return result


def build_runtime_model_probe_provenance(probe: Any) -> dict[str, Any]:
    """Record bounded caller-supplied probe evidence without reusing stale success."""
    base = {
        "status": "not_supplied",
        "source": "none",
        "freshness": "unknown",
        "stale_success_reused": False,
        "probe_executed_by_report": False,
        "model_switch_performed": False,
    }
    if probe is None:
        return base

    def invalid(reason: str) -> dict[str, Any]:
        return {**base, "status": "invalid_input", "reason": reason}

    if not isinstance(probe, dict):
        return invalid("invalid_runtime_model_probe")

    try:
        gateway = _valid_plain_text(probe.get("gateway"), limit=2048)
        model = _valid_plain_text(probe.get("model"), limit=512)
        result = _valid_plain_text(probe.get("result"), limit=64)
        observed_date = _valid_plain_text(
            probe.get("observed_date"), limit=32, allow_empty=True
        )
        latency_ms = int(probe.get("latency_ms"))
        timeout_ms = int(probe.get("timeout_ms"))
    except (TypeError, ValueError):
        return invalid("invalid_runtime_model_probe")
    if latency_ms < 0 or timeout_ms <= 0 or latency_ms > timeout_ms:
        return invalid("invalid_probe_timing")

    raw_advertised = probe.get("advertised_models", [])
    if not isinstance(raw_advertised, list) or len(raw_advertised) > MAX_RUNTIME_MODELS:
        return invalid("invalid_advertised_models")
    advertised: list[dict[str, Any]] = []
    for item in raw_advertised:
        if not isinstance(item, dict):
            return invalid("invalid_advertised_model")
        try:
            item_model = _valid_plain_text(item.get("model"), limit=512)
            state = _valid_plain_text(item.get("state"), limit=64)
        except ValueError:
            return invalid("invalid_advertised_model")
        loaded = item.get("loaded")
        if not isinstance(loaded, bool):
            return invalid("invalid_advertised_model")
        advertised.append({"model": item_model, "loaded": loaded, "state": state})

    return {
        "status": "observed",
        "source": "caller_supplied_current_observation",
        "freshness": "current_observation",
        "observed_date": observed_date,
        "gateway": gateway,
        "model": model,
        "result": result,
        "latency_ms": latency_ms,
        "timeout_ms": timeout_ms,
        "advertised_models": advertised,
        "stale_success_reused": False,
        "probe_executed_by_report": False,
        "model_switch_performed": False,
    }


def build_provenance_report(
    *,
    paper_analysis: dict[str, Any],
    exploration: Any = None,
    dpo: Any = None,
    runtime_model_probe: Any = None,
) -> dict[str, Any]:
    dream = build_exploration_provenance(exploration)
    preference = build_dpo_provenance(dpo)
    runtime_probe = build_runtime_model_probe_provenance(runtime_model_probe)
    status = "ok" if paper_analysis.get("status") == "ok" else "paper_unavailable"
    if (
        dream.get("status") in {"invalid_input", "policy_violation"}
        or preference.get("status") == "invalid_input"
        or runtime_probe.get("status") == "invalid_input"
    ):
        status = "input_invalid"
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "kind": "dream_rsi_research_provenance",
        "generated_at_unix": int(time.time()),
        "status": status,
        "paper_analysis": paper_analysis,
        "dream_rsi_exploration": dream,
        "dpo_preference_evaluation": preference,
        "runtime_model_probe": runtime_probe,
        "model_policy": {
            "automatic_promotion": False,
            "automatic_replacement": False,
        },
    }


def _read_bounded_stdin(enabled: bool) -> dict[str, Any]:
    if not enabled:
        return {}
    raw = sys.stdin.buffer.read(MAX_INPUT_JSON_BYTES + 1)
    if len(raw) > MAX_INPUT_JSON_BYTES:
        raise ValueError("input_too_large")
    if not raw.strip():
        return {}
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("invalid_input_json") from exc
    if not isinstance(payload, dict):
        raise ValueError("invalid_input_shape")
    return payload


def _emit_bounded_json(payload: dict[str, Any]) -> bool:
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    encoded = rendered.encode("utf-8")
    if len(encoded) > MAX_REPORT_JSON_BYTES:
        fallback = {
            "schema_version": REPORT_SCHEMA_VERSION,
            "kind": "dream_rsi_research_provenance",
            "status": "report_unavailable",
            "reason": "report_too_large",
        }
        print(json.dumps(fallback, ensure_ascii=False, indent=2, sort_keys=True))
        return False
    print(rendered)
    return True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build fail-closed DREAM-RSI paper/exploration/DPO provenance JSON."
    )
    parser.add_argument("--query", default=DEFAULT_QUERY, help="alphaXiv paper search query")
    parser.add_argument(
        "--paper-id",
        default=DREAM_RSI_PAPER_ID,
        help=f"exact arXiv paper id (default: DREAM-RSI {DREAM_RSI_PAPER_ID})",
    )
    parser.add_argument(
        "--alphaxiv-cli",
        default="alphaxiv",
        help="bare alphaxiv executable name or absolute executable path",
    )
    parser.add_argument(
        "--paper-source",
        choices=("auto", "alphaxiv", "orx"),
        default="auto",
        help="paper provider: prefer alphaxiv then orx, or force one provider",
    )
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument(
        "--provenance-stdin",
        action="store_true",
        help="read bounded exploration/DPO JSON from stdin",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload = _read_bounded_stdin(args.provenance_stdin)
    except ValueError as exc:
        _emit_bounded_json(
            {
                "schema_version": REPORT_SCHEMA_VERSION,
                "kind": "dream_rsi_research_provenance",
                "status": "input_invalid",
                "reason": str(exc),
                "model_policy": {
                    "automatic_promotion": False,
                    "automatic_replacement": False,
                },
            }
        )
        return 2

    paper = collect_paper_report(
        args.query,
        alphaxiv_cli=args.alphaxiv_cli,
        paper_source=args.paper_source,
        timeout=args.timeout,
        paper_id=args.paper_id,
    )
    report = build_provenance_report(
        paper_analysis=paper,
        exploration=payload.get("exploration"),
        dpo=payload.get("dpo"),
        runtime_model_probe=payload.get("runtime_model_probe"),
    )
    emitted = _emit_bounded_json(report)
    return 0 if emitted and report["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
