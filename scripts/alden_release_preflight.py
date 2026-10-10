#!/usr/bin/env python3
"""Read-only, fail-closed comparison of an Alden source clone and installed app.

This reports evidence for an operator. It NEVER installs, signs, stops,
restarts, changes Git refs, migrates databases, loads models or sends messages.
No private file contents, dirty filenames, symlink destinations or credentials
are included in its JSON output. Notarization and live integration are not
attested by a bundle file or a successful source-level check.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import stat
import subprocess
import sys

MAX_CONFIG_BYTES = 1024 * 1024
MAX_SOURCE_BYTES = 8 * 1024 * 1024
MAX_STATUS_BYTES = 32 * 1024 * 1024
RUN_TIMEOUT_SECONDS = 10
RESOURCE_SOURCES = (
    "scripts/alden_voice.py",
    "scripts/alden_tool_runtime.py",
    "scripts/alden_collection.py",
    "scripts/alden_collection_retrieval.py",
    "scripts/alden_knowledge_mcp.py",
)
SHA_PATTERN = re.compile(r"[0-9a-f]{40}\Z")


def _absolute(path: Path) -> Path:
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("preflight_path_invalid")
    return path


def _read_regular(path: Path, budget: int) -> bytes:
    _absolute(path)
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | os.O_NONBLOCK)
    except FileNotFoundError as error:
        raise ValueError("preflight_file_missing") from error
    except OSError as error:
        raise ValueError("preflight_file_unavailable") from error
    try:
        with os.fdopen(fd, "rb") as source:
            before = os.fstat(source.fileno())
            if not stat.S_ISREG(before.st_mode) or not 0 <= before.st_size <= budget:
                raise ValueError("preflight_file_invalid")
            payload = source.read(budget + 1)
            after = os.fstat(source.fileno())
        current = path.lstat()
        identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        identity_now = (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns)
        if (identity_before != identity_after or identity_after != identity_now
                or not stat.S_ISREG(current.st_mode) or len(payload) != before.st_size):
            raise ValueError("preflight_file_changed")
        return payload
    except OSError as error:
        raise ValueError("preflight_file_unavailable") from error


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _optional_file(path: Path | None) -> dict:
    if path is None:
        return {"state": "not_requested"}
    try:
        data = _read_regular(path, MAX_STATUS_BYTES)
        return {"state": "read_only_sha_verified", "sha256": _hash(data), "bytes": len(data)}
    except ValueError as error:
        return {"state": str(error)}


def _run_readonly(args: list[str], *, cwd: Path | None = None) -> tuple[str | None, bool]:
    # GIT_OPTIONAL_LOCKS=0 prevents Git from opportunistically refreshing
    # its index while probing user-owned, possibly dirty working trees.
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"}
    try:
        completed = subprocess.run(args, cwd=cwd, env=env, check=False,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   timeout=RUN_TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        return None, False
    if completed.returncode != 0 or len(completed.stdout) > MAX_CONFIG_BYTES:
        return None, False
    try:
        return completed.stdout.decode("utf-8", "strict").strip(), True
    except UnicodeDecodeError:
        return None, False


def _git(workspace: Path) -> dict:
    base = ["git", "-c", "core.fsmonitor=false", "-C", str(workspace)]
    head, head_ok = _run_readonly([*base, "rev-parse", "--verify", "HEAD"])
    top, top_ok = _run_readonly([*base, "rev-parse", "--show-toplevel"])
    branch, _ = _run_readonly([*base, "symbolic-ref", "--short", "-q", "HEAD"])
    status, status_ok = _run_readonly(
        [*base, "status", "--porcelain=v1", "--untracked-files=normal"]
    )
    # Never export names from git status to a remote report.
    revision = head if head_ok and isinstance(head, str) and SHA_PATTERN.fullmatch(head) else None
    return {
        "revision": revision,
        "branch": branch if isinstance(branch, str) and re.fullmatch(r"[A-Za-z0-9._/-]{1,128}", branch) else None,
        "exact_workspace_root": top_ok and top == str(workspace),
        "worktree": "dirty" if status_ok and status else "clean" if status_ok else "unknown",
    }


def _info(app: Path) -> dict:
    if app.is_symlink() or not app.is_dir():
        return {"state": "not_found"}
    try:
        raw = _read_regular(app / "Contents" / "Info.plist", MAX_CONFIG_BYTES)
        data = plistlib.loads(raw)
    except (ValueError, plistlib.InvalidFileException, TypeError, OSError):
        return {"state": "unreadable"}
    if not isinstance(data, dict):
        return {"state": "unreadable"}
    return {
        "state": "readable",
        "bundle_id": data.get("CFBundleIdentifier") if isinstance(data.get("CFBundleIdentifier"), str) else None,
        "version": data.get("CFBundleShortVersionString") if isinstance(data.get("CFBundleShortVersionString"), str) else None,
        "executable": data.get("CFBundleExecutable") if isinstance(data.get("CFBundleExecutable"), str) else None,
    }


def _signature(app: Path, *, present: bool) -> dict:
    # codesign verifies the signature cryptographically, not notarization,
    # Apple trust, entitlement correctness, or actual Mac application startup.
    if not present or not Path("/usr/bin/codesign").is_file():
        return {"state": "not_checked"}
    output, ok = _run_readonly(
        ["/usr/bin/codesign", "--verify", "--strict", str(app)]
    )
    del output
    return {"state": "signature_verified" if ok else "unsigned_or_invalid",
            "notarization": "not_checked"}


def check(workspace: Path, *, app: Path | None = None,
          goal: Path | None = None, status: Path | None = None,
          expected_sha: str | None = None) -> dict:
    workspace = _absolute(workspace)
    if workspace.is_symlink() or not workspace.is_dir():
        raise ValueError("preflight_workspace_invalid")
    if expected_sha is not None and not SHA_PATTERN.fullmatch(expected_sha):
        raise ValueError("preflight_expected_sha_invalid")
    config = json.loads(_read_regular(workspace / "desktop/src-tauri/tauri.conf.json", MAX_CONFIG_BYTES))
    if not isinstance(config, dict) or not isinstance(config.get("bundle"), dict):
        raise ValueError("preflight_config_invalid")
    resources = config["bundle"].get("resources")
    if not isinstance(resources, dict):
        raise ValueError("preflight_config_invalid")
    source = _git(workspace)
    origin = {
        "revision": source["revision"],
        "branch": source["branch"],
        "worktree": source["worktree"],
        "exact_workspace_root": source["exact_workspace_root"],
        "expected_sha_match": None if expected_sha is None else source["revision"] == expected_sha,
    }
    installed = _info(_absolute(app)) if app is not None else {"state": "not_requested"}
    matching = {}
    for source_name in RESOURCE_SOURCES:
        configured = resources.get("bundle-resources/" + source_name)
        # Never make a bundle allowlist from a guessed or dynamic filename.
        if configured != source_name:
            matching[source_name] = "resource_allowlist_missing"
            continue
        source_path = workspace / source_name
        try:
            current_digest = _hash(_read_regular(source_path, MAX_SOURCE_BYTES))
        except ValueError:
            matching[source_name] = "source_missing_or_invalid"
            continue
        if installed["state"] != "readable" or app is None:
            matching[source_name] = "installed_bundle_not_readable"
            continue
        try:
            bundle_digest = _hash(_read_regular(app / "Contents/Resources" / source_name, MAX_SOURCE_BYTES))
        except ValueError:
            matching[source_name] = "bundle_file_missing_or_invalid"
            continue
        matching[source_name] = "exact_bytes_match" if current_digest == bundle_digest else "source_bundle_mismatch"
    config_id, config_version = config.get("identifier"), config.get("version")
    installed["bundle_id_matches_source"] = installed.get("bundle_id") == config_id if installed["state"] == "readable" else False
    installed["version_matches_source"] = installed.get("version") == config_version if installed["state"] == "readable" else False
    installed["executable_matches"] = installed.get("executable") == "openkakao-alden-desktop" if installed["state"] == "readable" else False
    installed["resource_bindings"] = matching
    installed["signature"] = _signature(app, present=installed["state"] == "readable") if app else {"state": "not_checked"}
    # File metadata are only a checkpoint and are never a substitute for
    # reading the actual goal or STATUS content on the authorized Mac.
    evidence = {
        "goal": _optional_file(_absolute(goal) if goal is not None else None),
        "status": _optional_file(_absolute(status) if status is not None else None),
    }
    blockers = []
    if not source["exact_workspace_root"] or source["worktree"] == "unknown":
        blockers.append("source_workspace_not_verified")
    if source["worktree"] == "dirty":
        blockers.append("uncommitted_changes_preserve_before_install")
    if expected_sha is None or source["revision"] != expected_sha:
        blockers.append("source_sha_not_pinned")
    if installed["state"] != "readable":
        blockers.append("installed_bundle_not_readable")
    if not all(installed[key] for key in ("bundle_id_matches_source", "version_matches_source", "executable_matches")):
        blockers.append("bundle_identity_not_verified")
    if any(value != "exact_bytes_match" for value in matching.values()):
        blockers.append("bundle_source_not_identical")
    if installed["signature"]["state"] != "signature_verified":
        blockers.append("signature_not_verified")
    # Even a fully matching bundle still needs live E2E, preserved backups,
    # optional notarization and explicit installation/release authority.
    blockers.extend(["live_mac_e2e_not_verified", "operator_install_authorization_required"])
    return {
        "ok": True,
        "operation": "read_only_preflight",
        "source": origin,
        "installed": installed,
        "evidence": evidence,
        "release_gate": {"ready_to_deploy": False, "blockers": blockers},
        "physical_runtime_verified": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--app", type=Path)
    parser.add_argument("--goal", type=Path)
    parser.add_argument("--status", type=Path)
    parser.add_argument("--expected-sha")
    args = parser.parse_args(argv)
    try:
        result = check(args.workspace, app=args.app, goal=args.goal,
                       status=args.status, expected_sha=args.expected_sha)
    except (ValueError, OSError, json.JSONDecodeError) as error:
        # Constant external error codes, no private paths or file contents.
        result = {"ok": False, "error": str(error) if str(error).startswith("preflight_") else "preflight_unavailable"}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
