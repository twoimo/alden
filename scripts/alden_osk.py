#!/usr/bin/env python3
"""Alden's local OSK adapter. No hooks, MCP registration, Git sync or LLM calls.

OSK v4.1.2 is bundled unchanged. Its public write API validates every imported
node; its graph/contract APIs read the vault back. The SQLite ERE index remains
the evidence source. Source relationships keep their meaning in the projection
and body links, rather than being mislabeled OSK `derived-from` authority.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
import zipfile
from pathlib import Path

VERSION = "v4.1.2"
COMMIT = "9bbf08febc5a1fb2af068006735ed79cbdb71178"
ARCHIVE_HASH = "74feda64efb68c819a45746359de4158e426f87d74f682602f23fb6d46c9d4e3"
MAX_MUTATIONS = 32  # Persist progress after every node; larger vaults converge over ticks.
MAX_BODY = 12000
_ENGINE = None
_ROOT = None


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _revision(node: dict, body: str) -> str:
    semantic = {key: value for key, value in node.items() if key != "updated_at"}
    return digest(json.dumps(semantic, ensure_ascii=False, sort_keys=True).encode() + body.encode())


def _safe_directory(path: Path) -> Path:
    path = path.absolute()
    for parent in reversed([path, *path.parents]):
        if parent.is_symlink():
            raise RuntimeError("osk_symlink_path")
        parent.mkdir(mode=0o700, exist_ok=True)
        if not parent.is_dir():
            raise RuntimeError("osk_directory_invalid")
    return path


def _read_json(path: Path, fallback: dict | None = None) -> dict:
    if path.is_symlink():
        raise RuntimeError("osk_symlink_file")
    try:
        if path.stat().st_size > 16 * 1024 * 1024:
            raise RuntimeError("osk_state_too_large")
        result = json.loads(path.read_text())
        if not isinstance(result, dict):
            raise RuntimeError("osk_state_invalid")
        return result
    except FileNotFoundError:
        return {} if fallback is None else fallback


def _save(path: Path, value: dict) -> None:
    if path.is_symlink():
        raise RuntimeError("osk_symlink_file")
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".osk-save-")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def _home(state_root: Path) -> Path:
    return state_root / "knowledge" / "osk"


def _load_engine(state_root: Path):
    global _ENGINE, _ROOT
    home = _safe_directory(_home(state_root))
    if _ENGINE is not None:
        if _ROOT != home:
            raise RuntimeError("osk_process_vault_changed")
        return _ENGINE
    vendor = Path(__file__).parent / "vendor"
    archive = vendor / f"osk-{VERSION}.zip"
    manifest = _read_json(vendor / f"osk-{VERSION}.json")
    if archive.is_symlink() or digest(archive.read_bytes()) != ARCHIVE_HASH:
        raise RuntimeError("osk_archive_integrity")
    if manifest.get("archive_sha256") != ARCHIVE_HASH or manifest.get("commit") != COMMIT:
        raise RuntimeError("osk_manifest_integrity")
    destination = _safe_directory(home / "engines") / ARCHIVE_HASH[:16]
    if destination.is_symlink():
        raise RuntimeError("osk_symlink_engine")
    if not destination.exists():
        stage = Path(tempfile.mkdtemp(dir=destination.parent, prefix=".engine-"))
        try:
            with zipfile.ZipFile(archive) as bundle:
                if set(bundle.namelist()) != set(manifest["files"]):
                    raise RuntimeError("osk_archive_manifest_mismatch")
                for name, expected in manifest["files"].items():
                    relative = Path(name)
                    if relative.is_absolute() or ".." in relative.parts:
                        raise RuntimeError("osk_archive_path")
                    data = bundle.read(name)
                    if digest(data) != expected:
                        raise RuntimeError("osk_archive_member_integrity")
                    target = stage / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
            stage.rename(destination)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
    for name, expected in manifest["files"].items():
        target = destination / name
        if any(p.is_symlink() for p in [target, *target.parents]) or digest(target.read_bytes()) != expected:
            raise RuntimeError("osk_engine_integrity")
    vault = _safe_directory(home / "vault")
    for name in ("00_Scope", "00_Domain", "00_Person", "00_Scope/Alden"):
        _safe_directory(vault / name)
    governance = _safe_directory(vault / "_governance")
    for name in ("Constitution.md", "Bylaws.md", "Mechanism.md", "Workbench-Contract.md"):
        target = governance / name
        if target.is_symlink():
            raise RuntimeError("osk_symlink_governance")
        if not target.exists():
            target.write_bytes((destination / "_governance" / name).read_bytes())
    os.environ["OSK_VAULT_ROOT"] = str(vault)
    sys.path[:0] = [str(destination / "deps"), str(destination / "_governance/_engine")]
    from osk import contract, graph, secrets, write
    _ENGINE = (contract, graph, secrets, write)
    _ROOT = home
    return _ENGINE


def _aborted(state_root: Path) -> bool:
    from alden_abort import read_abort_state
    return read_abort_state(state_root / "alden-abort.json").latched


def _clean(text, secrets, limit: int = MAX_BODY) -> str:
    # Never let source text create arbitrary wiki references or Markdown sections.
    value = secrets.filter_text(str(text or ""))[0]
    return value.replace("[[", "［［").replace("]]", "］］")[:limit]


def _title(node: dict, secrets) -> str:
    label = _clean(node.get("label"), secrets, 48)
    label = re.sub(r'[<>:"/\\|?*#\]\[\x00-\x1f]', "·", label).strip(" .")
    return f"{label or '기억'} · {digest(str(node['id']).encode())[:10]}"


def _body(node: dict, links: list[dict], titles: dict[str, str], secrets) -> str:
    lines = ["## 올든이 관리하는 대화 지식", "", f"출처 ID: `{_clean(node['id'], secrets, 192)}`",
             f"유형: {_clean(node.get('category'), secrets, 96)}", "",
             _clean(node.get("description"), secrets, 1800)]
    lines.extend("- " + _clean(fact, secrets, 600).replace("\n", " ") for fact in node.get("facts", [])[:12])
    evidence = node.get("evidence") or {}
    lines += ["", "## 출처", json.dumps(evidence, ensure_ascii=False, sort_keys=True)]
    if links:
        lines += ["", "## 대화에서 발견한 관계"]
    for edge in links[:24]:
        target = edge["target"] if edge["source"] == node["id"] else edge["source"]
        if target in titles:
            lines.append(f"- {_clean(edge.get('relation'), secrets, 96)}: [[{titles[target]}]]")
    return secrets.filter_text("\n".join(lines))[0][:MAX_BODY]


def synchronize(state_root: Path, source: dict | None = None) -> dict:
    """One bounded, replayable tick. Source deletion retracts, never purges notes."""
    home = _safe_directory(_home(state_root))
    lock_fd = os.open(home / "sync.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"ok": True, "state": "busy", "engine": VERSION}
        if _aborted(state_root):
            return {"ok": False, "state": "paused", "engine": VERSION}
        contract, graph, secrets, write = _load_engine(state_root)
        if source is None:
            from auto_reply_knowledge_graph import collect_knowledge_graph
            source = collect_knowledge_graph(state_root / "context.sqlite3", state_root=state_root)
        if source.get("ok") is not True:
            raise RuntimeError("osk_source_unavailable")
        source = json.loads(secrets.filter_text(json.dumps(source, ensure_ascii=False))[0])
        checkpoint_path = home / "sync.json"
        checkpoint = _read_json(checkpoint_path, {"managed": {}, "generation": 0})
        managed = checkpoint["managed"]
        nodes = {str(n["id"]): n for n in source.get("nodes", []) if not str(n["id"]).startswith(("message:", "msg:"))}
        titles = {key: managed.get(key, {}).get("title") or _title(node, secrets) for key, node in nodes.items()}
        hub = home / "vault/00_Scope/Alden/Alden.md"
        if not hub.exists():
            write.create_node("Alden", "올든의 대화에서 얻은 지식과 그 출처", "올든이 관리하는 로컬 대화 지식입니다.", "agent", space="00_Scope/Alden")
        changed = 0
        conflicts = 0
        for key, node in sorted(nodes.items()):
            if _aborted(state_root):
                break
            old = managed.get(key)
            title = old["title"] if old else titles[key]
            titles[key] = title
            relations = [e for e in source.get("edges", []) if e.get("source") == key or e.get("target") == key]
            body = _body(node, relations, titles, secrets)
            revision = _revision(node, body)
            path = home / "vault/00_Scope/Alden" / f"{title}.md"
            if path.is_symlink():
                raise RuntimeError("osk_symlink_note")
            current = path.read_bytes() if path.exists() else None
            current_hash = digest(current) if current is not None else ""
            if old and current_hash != old.get("written_hash"):
                conflicts += 1
                old["held"] = True
                continue  # Human edit, missing file, or protection: preserve and report.
            if old and old.get("revision") == revision:
                old["active"] = True
                old["held"] = False
                old["source"] = node
                continue
            if changed >= MAX_MUTATIONS:
                continue
            summary = _clean(node.get("description") or node.get("label"), secrets, 78).replace("\n", " ").replace("［［", "") or "대화에서 찾은 지식"
            try:
                if current is None:
                    result = write.create_node(title, summary, body, "agent", space="00_Scope/Alden")
                elif old:
                    history = _safe_directory(home / "history" / digest(key.encode())[:16])
                    backup = history / f"{current_hash}.md"
                    if not backup.exists():
                        backup.write_bytes(current)
                    result = write.update_node(title, body=body, expect_hash=current_hash, summary=summary)
                else:
                    # Recover an interrupted create only after exact persisted readback.
                    recovered = contract.parse(path)
                    if recovered.body.strip() != body.strip() or recovered.meta.get("drafter") != "agent":
                        conflicts += 1
                        continue
                    result = {"id": recovered.id, "new_hash": current_hash}
            except write.WriteError:
                conflicts += 1
                continue
            parsed = contract.parse(path)
            if contract.validate(parsed):
                raise RuntimeError("osk_written_contract_invalid")
            managed[key] = {"title": title, "osk_id": parsed.id, "written_hash": digest(path.read_bytes()),
                            "revision": revision, "active": True, "held": False, "source": node}
            changed += 1
            _save(checkpoint_path, checkpoint)
        # Do not retract while the source reindex is pending or cancellation occurs.
        if not source.get("stale") and not _aborted(state_root):
            for key, item in managed.items():
                if key not in nodes:
                    item["active"] = False
        active = {key for key, item in managed.items() if item.get("active")}
        checkpoint.update({"engine": VERSION, "commit": COMMIT, "generation": checkpoint.get("generation", 0) + 1,
                           "synced_at": int(time.time()), "source_indexed_at": source.get("indexed_at", 0),
                           "stale": bool(source.get("stale")), "changed": changed, "conflicts": conflicts,
                           "pending": sum(managed.get(k, {}).get("revision") != _revision(n, _body(n, [e for e in source.get("edges", []) if k in (e.get("source"), e.get("target"))], titles, secrets)) for k, n in nodes.items()),
                           "edges": [e for e in source.get("edges", []) if e.get("source") in active and e.get("target") in active]})
        _save(checkpoint_path, checkpoint)
        return {"ok": True, **{k: checkpoint[k] for k in ("engine", "synced_at", "changed", "pending", "conflicts", "stale")}}
    finally:
        os.close(lock_fd)


def read_graph(state_root: Path) -> dict:
    home = _home(state_root)
    checkpoint = _read_json(home / "sync.json")
    if not checkpoint:
        return {"ok": False, "nodes": [], "edges": [], "stale": True, "osk": {"state": "preparing", "engine": VERSION}}
    contract, graph, _, _ = _load_engine(state_root)
    idx = graph.Index()
    nodes, identities = [], {}
    for key, item in checkpoint.get("managed", {}).items():
        if not item.get("active"):
            continue
        resolved = idx.nodes.get(item["title"])
        if not resolved:
            continue
        note = contract.parse(resolved[0])
        if contract.validate(note) or note.id != item.get("osk_id"):
            continue
        nodes.append({**item["source"], "description": str(note.meta.get("summary", "")), "facts": [str(note.meta.get("summary", ""))]})
        identities[item["title"]] = key
    # Human-created ordinary OSK notes participate too; governance/hub never does.
    for title, (path, kind) in idx.nodes.items():
        if title in identities or kind[0] in ("governance", "workbench", "archive") or graph.is_hub(path):
            continue
        note = contract.parse(path)
        if contract.validate(note):
            continue
        if any(item.get("osk_id") == note.id for item in checkpoint.get("managed", {}).values()):
            continue  # A retracted imported note remains recoverable, not searchable.
        key = "osk:" + note.id
        identities[title] = key
        nodes.append({"id": key, "label": title, "category": "memory", "description": str(note.meta.get("summary", "")),
                      "importance": 45, "updated_at": int(path.stat().st_mtime), "evidence": {"kind": "seed"}})
    ids = {n["id"] for n in nodes}
    edges = [e for e in checkpoint.get("edges", []) if e.get("source") in ids and e.get("target") in ids]
    existing = {(e["source"], e["target"]) for e in edges}
    for title, key in identities.items():
        note = contract.parse(idx.nodes[title][0])
        for target in note.wikilinks():
            if target in identities and identities[target] != key and (key, identities[target]) not in existing:
                edges.append({"source": key, "target": identities[target], "relation": "linked", "weight": 1, "evidence": {"kind": "seed"}})
    stale = checkpoint.get("stale", True) or int(time.time()) - checkpoint.get("synced_at", 0) > 180
    return {"ok": True, "nodes": nodes, "edges": edges, "node_count": len(nodes), "edge_count": len(edges),
            "indexed_at": checkpoint.get("source_indexed_at", 0), "stale": stale,
            "osk": {"state": "paused" if _aborted(state_root) else "ready", "engine": VERSION,
                    "synced_at": checkpoint.get("synced_at", 0), "pending": checkpoint.get("pending", 0),
                    "conflicts": checkpoint.get("conflicts", 0)}}


def read_focus(state_root: Path, node_id: str) -> dict:
    home = _home(state_root)
    checkpoint = _read_json(home / "sync.json")
    if not checkpoint:
        return {"ok": False, "facts": [], "fact_count": 0}
    contract, graph, _, _ = _load_engine(state_root)
    idx = graph.Index()
    item = checkpoint.get("managed", {}).get(node_id)
    title = item.get("title") if item and item.get("active") else None
    if node_id.startswith("osk:"):
        match = idx.by_id.get(node_id[4:])
        title = match[0].stem if match else None
    if not title or title not in idx.nodes:
        return {"ok": False, "facts": [], "fact_count": 0}
    note = contract.parse(idx.nodes[title][0])
    if contract.validate(note):
        return {"ok": False, "facts": [], "fact_count": 0}
    return {"ok": True, "facts": [str(note.meta.get("summary", "")), note.body[:1200]], "fact_count": 2, "focus_node_id": node_id}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--sync", action="store_true")
    args = parser.parse_args()
    try:
        result = synchronize(args.state_root) if args.sync else read_graph(args.state_root)
    except Exception as error:
        result = {"ok": False, "state": "unavailable", "error": type(error).__name__}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
