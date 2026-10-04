#!/usr/bin/env python3
"""Alden's local OSK adapter. No hooks, MCP registration, Git sync or LLM calls.

OSK v4.1.2 is pinned with a documented adjacency performance patch. Its public
write API validates ordinary notes; graph/contract APIs read them back. Imported
Kakao records live in private non-node _sources with their original roles.
Ordinary notes use genuine derived-from source coordinates, separate from ERE
semantic relationships and from the human approval ledger.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile
from collections import Counter
from dataclasses import replace
from datetime import datetime, timezone
from math import isfinite
from pathlib import Path

VERSION = "v4.1.2"
COMMIT = "9bbf08febc5a1fb2af068006735ed79cbdb71178"
ARCHIVE_HASH = "74feda64efb68c819a45746359de4158e426f87d74f682602f23fb6d46c9d4e3"
MAX_MUTATIONS = 32  # Source writes, cluster creation and moves share one budget.
MAX_HUB_WRITES = 16
ORGANIZATION_VERSION = 2
MAX_BODY = 12000
_ENGINE = None
_ROOT = None
SOURCE_SPACE = '00_Scope/Alden/Alden 카카오톡'


def _resolve_note(item: dict, idx, contract):
    """Resolve one unambiguous stable ID; a reused title is never identity proof."""
    identity = item.get('osk_id')
    if not identity or identity in idx.dup_ids:
        return None
    found = idx.by_id.get(identity)
    if not found:
        return None
    path = found[0]
    if any(p.is_symlink() for p in [path, *path.parents]):
        raise RuntimeError('osk_symlink_note')
    data = path.read_bytes()
    note = contract.parse_bytes(path, data)
    if contract.validate(note) or note.id != identity:
        return None
    return path, note, digest(data)


def _same_note(home: Path, item: dict, resolved) -> bool:
    return bool(resolved and resolved[0].stem == item['title']
                and str(resolved[0].parent.relative_to(home / 'vault')) == item.get('space', '00_Scope/Alden')
                and resolved[2] == item.get('written_hash'))


def _reconcile_note(home: Path, item: dict, idx, contract):
    resolved = _resolve_note(item, idx, contract)
    if not resolved:
        item.update(held=True, organization_reason='osk_note_identity_unavailable')
        return None
    path, note, current_hash = resolved
    space = str(path.parent.relative_to(home / 'vault'))
    plan = item.get('planned_move', {})
    # An interrupted adapter move is adopted only at its persisted destination,
    # with the same ID, title and bytes. Other locations belong to the human.
    if (plan and not item.get('human_corrected') and plan.get('osk_id') == note.id
            and plan.get('title') == path.stem == item['title']
            and plan.get('from_space') == item.get('space', '00_Scope/Alden')
            and plan.get('dest_space') == space
            and plan.get('written_hash') == current_hash == item.get('written_hash')):
        item.update(space=space, held=False)
        item.pop('planned_move', None)
        item.pop('organization_reason', None)
    elif not _same_note(home, item, resolved) or item.get('human_corrected'):
        if path.stem != item['title']:
            item['human_title'] = True
        item.update(title=path.stem, space=space, held=True, human_corrected=True,
                    organization_reason='osk_human_change_preserved')
    else:
        item['held'] = False
        item.pop('organization_reason', None)
    item.setdefault('space', space)
    return resolved


def _placement_signature(home: Path, graph) -> str:
    return digest(json.dumps(sorted(_placements(home, graph)), ensure_ascii=False).encode())


def _context_plan(checkpoint: dict) -> tuple[dict, dict]:
    """Source locality, not entity type or an inferred semantic community.

    A single exact, existing source room admits a local branch. Memories spanning
    rooms (or lacking a resolvable room) stay at the source entry, with their real
    references intact. A speaker is not the user's Person facet.
    """
    active = {k: v for k, v in checkpoint['managed'].items() if v.get('active')}
    rooms = {}
    for key, item in active.items():
        node = item['source']
        if key.startswith('chat:') or node.get('category', '').casefold() in ('room', '대화방'):
            ref = node.get('evidence', {}).get('chat_id')
            if ref:
                rooms[str(ref)] = item
    contexts = {key: set() for key in active}
    for key, item in active.items():
        evidence = item['source'].get('evidence', {})
        contexts[key].update(str(r) for r in evidence.get('room_ids', []) if r)
        if evidence.get('chat_id'):
            contexts[key].add(str(evidence['chat_id']))
    for edge in checkpoint.get('edges', []):
        room = str(edge.get('room_id') or edge.get('evidence', {}).get('chat_id') or '')
        if room and not edge.get('evidence', {}).get('retracted'):
            for key in (edge.get('source'), edge.get('target')):
                if key in contexts:
                    contexts[key].add(room)
    assignments = {key: next(iter(refs)) if len(refs) == 1 and next(iter(refs)) in rooms else '' for key, refs in contexts.items()}
    return rooms, assignments


def _placements(home: Path, graph) -> list:
    return [(title, str(path.parent.relative_to(home / 'vault')), graph.is_hub(path))
            for title, (path, kind) in graph.Index().nodes.items()
            if kind[0] not in ('governance', 'workbench', 'archive')]


def _hub_body(item: dict, placements: list) -> str:
    space = item['space']
    # Navigation includes preserved historical/human notes too. A source
    # retraction excludes it from Alden's active graph, not from OSK reachability.
    targets = [title for title, child, is_hub in placements if title != item['title']
               and (child == space or is_hub and str(Path(child).parent) == space)]
    if item.get('legacy'):
        intro = '이전 유형별 배치의 보존된 입구입니다. 현재 이 위치에 남은 기억만 가리킵니다.'
    elif item.get('room_ref'):
        intro = '같은 채팅방 원문에서 확인한 대화 맥락입니다. 인물과 주제의 관계는 각 기억의 참조로 이어집니다.'
    elif space == SOURCE_SPACE:
        intro = '카카오톡 원문에서 얻은 기억입니다. 한 채팅방에 결속된 기억은 그 맥락으로, 여러 방에 걸친 기억은 이 입구에 둡니다.'
    else:
        intro = '올든이 관리하는 로컬 대화 지식과 출처별 입구입니다.'
    return intro + '\n\n' + '\n'.join('- [[' + title + ']]' for title in sorted(set(targets)))


def _layout_pending(checkpoint: dict, home: Path, contract, graph) -> int:
    rooms, assignments = _context_plan(checkpoint)
    hubs = checkpoint.get('organization', {}).get('hubs', {})
    placements = _placements(home, graph)
    pending = 0
    for key, ref in assignments.items():
        context = hubs.get('context:' + digest(ref.encode())) if ref else None
        dest = context['space'] if context else SOURCE_SPACE
        item = checkpoint['managed'][key]
        if (ref and (not context or context.get('held')) or item.get('held')
                or item.get('planned_move') or item.get('space') != dest):
            pending += 1
    idx = graph.Index()
    for hub in hubs.values():
        resolved = _resolve_note(hub, idx, contract)
        if not hub.get('held') and _same_note(home, hub, resolved):
            pending += resolved[1].body.strip() != _hub_body(hub, placements).strip()
        else:
            pending += 1
    pending += sum(key not in hubs for key in ('root', 'legacy:kakao'))
    return pending

def _organize_vault(home: Path, checkpoint: dict, contract, graph, write, budget: int) -> int:
    """Bounded SDK moves and CAS hub repair; pins and human edits stay authoritative."""
    organization = checkpoint.setdefault('organization', {'version': ORGANIZATION_VERSION, 'hubs': {}})
    hubs = organization['hubs']
    for key, old in checkpoint.get('groups', {}).items():
        hubs.setdefault('legacy:' + key, {**old, 'legacy': key != 'kakao'})
    idx = graph.Index()
    for hub in hubs.values():
        _reconcile_note(home, hub, idx, contract)
    for key, title, space in [('root', 'Alden', '00_Scope/Alden'), ('legacy:kakao', 'Alden 카카오톡', SOURCE_SPACE)]:
        if key in hubs:
            continue
        path = home / 'vault' / space / (title + '.md')
        if not path.exists():
            if budget <= 0:
                continue
            write.create_node(title, '출처별 대화 지식의 입구', '올든이 관리하는 로컬 대화 지식입니다.', 'agent', space=space)
            budget -= 1
        note = contract.parse(path)
        # Only adopt the adapter's known initial hub, never a human-authored body.
        if not contract.validate(note) and note.body.strip() == '올든이 관리하는 로컬 대화 지식입니다.':
            hubs[key] = {'title': title, 'space': space, 'osk_id': note.id, 'written_hash': digest(path.read_bytes())}
    rooms, assignments = _context_plan(checkpoint)
    for ref, room in sorted(rooms.items()):
        if _aborted(home.parent.parent):
            break
        key = 'context:' + digest(ref.encode())
        if key in hubs or budget <= 0:
            continue
        # Stable source coordinate, not a display-name identity or topic guess.
        title = '대화 맥락 · ' + room['title']
        space = SOURCE_SPACE + '/' + title
        path = home / 'vault' / space / (title + '.md')
        initial = '같은 채팅방 원문에서 확인한 기억을 잇는 입구입니다.'
        if not path.exists():
            write.create_node(title, initial, initial, 'agent', space=space)
            budget -= 1
        note = contract.parse(path)
        if contract.validate(note) or note.body.strip() != initial:
            continue
        hubs[key] = {'title': title, 'space': space, 'osk_id': note.id, 'written_hash': digest(path.read_bytes()), 'room_ref': ref}
        _save(home / 'sync.json', checkpoint)
    moved = 0
    for key, ref in sorted(assignments.items()):
        if _aborted(home.parent.parent):
            break
        item = checkpoint['managed'][key]
        context = hubs.get('context:' + digest(ref.encode())) if ref else None
        resolved = _reconcile_note(home, item, graph.Index(), contract)
        if ref and (not context or context.get('held')) or item.get('held') or not resolved:
            continue
        dest = context['space'] if context else SOURCE_SPACE
        if item.get('space', '00_Scope/Alden') == dest or budget <= 0:
            continue
        item['planned_move'] = {'osk_id': item['osk_id'], 'title': item['title'],
                                'from_space': item['space'], 'dest_space': dest,
                                'written_hash': item['written_hash']}
        _save(home / 'sync.json', checkpoint)
        try:
            result = write.move_node(item['osk_id'], dest)
        except write.WriteError as error:
            item.update(held=True, organization_reason=str(error)[:500])
            continue
        if not result.get('ok'):
            item.update(held=True, organization_reason='osk_move_incomplete')
            continue
        _reconcile_note(home, item, graph.Index(), contract)
        if item.get('held') or item.get('planned_move'):
            raise RuntimeError('osk_move_identity_changed')
        moved += 1
        budget -= 1
        _save(home / 'sync.json', checkpoint)
    repaired = 0
    placements = _placements(home, graph)
    for hub in hubs.values():
        if repaired >= MAX_HUB_WRITES or _aborted(home.parent.parent):
            break
        resolved = _reconcile_note(home, hub, graph.Index(), contract)
        if not resolved or hub.get('held'):
            continue
        path, note, _ = resolved
        body = _hub_body(hub, placements)
        if note.body.strip() == body.strip():
            continue
        try:
            write.update_node(hub['osk_id'], body=body, expect_hash=hub['written_hash'])
        except write.WriteError as error:
            hub.update(held=True, organization_reason=str(error)[:500])
            continue
        hub['written_hash'] = digest(path.read_bytes())
        repaired += 1
        _save(home / 'sync.json', checkpoint)
    organization.update(version=ORGANIZATION_VERSION, hub_writes=repaired)
    return moved

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


def _load_engine(state_root: Path, *, read_only: bool = False):
    global _ENGINE, _ROOT
    home = _home(state_root).absolute() if read_only else _safe_directory(_home(state_root))
    if read_only and (not home.is_dir() or any(p.is_symlink() for p in [home, *home.parents])):
        raise RuntimeError('osk_read_only_vault_unavailable')
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
    destination = (home / 'engines' if read_only else _safe_directory(home / "engines")) / ARCHIVE_HASH[:16]
    if destination.is_symlink():
        raise RuntimeError("osk_symlink_engine")
    if not destination.exists():
        if read_only:
            raise RuntimeError('osk_read_only_engine_unavailable')
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
    vault = home / 'vault' if read_only else _safe_directory(home / "vault")
    for name in ("00_Scope", "00_Domain", "00_Person", "00_Scope/Alden"):
        if read_only:
            if not (vault / name).is_dir() or any(p.is_symlink() for p in [vault / name, *(vault / name).parents]):
                raise RuntimeError('osk_read_only_space_unavailable')
        else:
            _safe_directory(vault / name)
    governance = vault / '_governance' if read_only else _safe_directory(vault / "_governance")
    for name in ("Constitution.md", "Bylaws.md", "Mechanism.md", "Workbench-Contract.md"):
        target = governance / name
        if target.is_symlink() or read_only and any(p.is_symlink() for p in target.parents):
            raise RuntimeError("osk_symlink_governance")
        if not target.exists():
            if read_only:
                raise RuntimeError('osk_read_only_governance_unavailable')
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


def _date_epoch(value) -> float | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        date = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
        date = date if date.tzinfo else date.replace(tzinfo=timezone.utc)
        epoch = date.timestamp()
        return epoch if isfinite(epoch) else None
    except (ValueError, OverflowError, OSError):
        return None


def _edge_active(edge: dict, *, now: float | None = None) -> bool:
    if (edge.get('evidence') or {}).get('retracted'):
        return False
    now = time.time() if now is None else now
    start, end = _date_epoch(edge.get('valid_from')), _date_epoch(edge.get('valid_to'))
    return (start is None or start <= now) and (end is None or now < end)


def _next_transition_at(edges: list[dict], now: float) -> float:
    """Next start-inclusive/end-exclusive semantic boundary, or zero if none."""
    boundaries = []
    for edge in edges:
        if ((edge.get('evidence') or {}).get('retracted')
                or edge.get('purpose') in ('navigation', 'reference')):
            continue
        start, end = _date_epoch(edge.get('valid_from')), _date_epoch(edge.get('valid_to'))
        if start is not None and end is not None and end <= start:
            continue  # An empty/reversed interval is never active.
        boundaries.extend(boundary for boundary in (start, end) if boundary is not None and boundary > now)
    return min(boundaries, default=0)


def _active_relation_signature(checkpoint: dict) -> str:
    now = time.time()
    return digest(json.dumps([e for e in checkpoint.get('edges', []) if _edge_active(e, now=now)],
                             ensure_ascii=False, sort_keys=True).encode())


def _body(node: dict, links: list[dict], titles: dict[str, str], secrets, *, legacy: bool = False) -> str:
    lines = ["## 올든이 관리하는 대화 지식", "", f"출처 ID: `{_clean(node['id'], secrets, 192)}`",
             f"유형: {_clean(node.get('category'), secrets, 96)}", "",
             _clean(node.get("description"), secrets, 1800)]
    lines.extend("- " + _clean(fact, secrets, 600).replace("\n", " ") for fact in node.get("facts", [])[:12])
    evidence = node.get("evidence") or {}
    lines += ["", "## 출처", json.dumps(evidence, ensure_ascii=False, sort_keys=True)]
    links = links if legacy else [edge for edge in links if _edge_active(edge)]
    if links:
        lines += [""] + ([] if legacy else ['<!-- alden:relations:start -->']) + ["## 대화에서 발견한 관계"]
    for edge in links[:24]:
        target = edge["target"] if edge["source"] == node["id"] else edge["source"]
        if target in titles:
            lines.append(f"- {_clean(edge.get('relation'), secrets, 96)}: [[{titles[target]}]]")
    if links and not legacy:
        lines.append('<!-- alden:relations:end -->')
    return secrets.filter_text("\n".join(lines))[0][:MAX_BODY]


def _relation_block(body: str, *, legacy: bool = False) -> str:
    marker = '\n\n## 대화에서 발견한 관계' if legacy else '\n\n<!-- alden:relations:start -->'
    _, separator, tail = body.partition(marker)
    return separator + tail if separator else ''


def _automatic_block(item: dict, checkpoint: dict, secrets) -> str:
    if 'automatic_relation_block' in item:
        return item['automatic_relation_block']
    # Upgrade provenance for the previous adapter format without touching its
    # notes. Only exact generated text is excluded; separately authored Links stay.
    node = item.get('source', {})
    links = [e for e in checkpoint.get('edges', []) if node.get('id') in (e.get('source'), e.get('target'))]
    titles = {key: value['title'] for key, value in checkpoint.get('managed', {}).items()}
    return _relation_block(_body(node, links, titles, secrets, legacy=True), legacy=True) if node else ''


def _reference_body(note, item: dict | None, checkpoint: dict, secrets) -> str:
    block = _automatic_block(item, checkpoint, secrets) if item else ''
    if not block:
        return note.body
    if note.body.count(block) == 1:
        return note.body.replace(block, '', 1)
    # A human may add a normal Link inside the generated section. Exclude only
    # unchanged, recorded automatic rows, retaining additions and edited rows.
    marker = '\n\n<!-- alden:relations:start -->' if '<!-- alden:relations:start -->' in block else '\n\n## 대화에서 발견한 관계'
    if note.body.count(marker) != 1:
        return note.body
    head, separator, tail = note.body.partition(marker)
    if marker.endswith('-->'):
        region, end, suffix = tail.partition('<!-- alden:relations:end -->')
        if not end:
            return note.body
    else:
        boundary = re.search(r'\n#{1,2} ', tail)
        region, end, suffix = (tail[:boundary.start()], '', tail[boundary.start():]) if boundary else (tail, '', '')
    rows = Counter(line for line in block.splitlines() if line.startswith('- ') and '[[' in line)
    kept = []
    for line in region.splitlines(keepends=True):
        row = line.rstrip('\r\n')
        if rows[row]:
            rows[row] -= 1
        else:
            kept.append(line)
    return head + separator + ''.join(kept) + end + suffix


def _source_signature(state_root: Path) -> str:
    pointer=_read_json(state_root/'knowledge/corpus/current.json')
    if pointer.get('schema_version')!=1:return ''
    # Source and derivation version both invalidate the materialized graph.
    files=['alden_osk.py','alden_osk_sources.py','alden_corpus_topics.py','auto_reply_reference_store.py','auto_reply_knowledge_graph.py']
    code=b''.join(digest(Path(__file__).with_name(name).read_bytes()).encode() for name in files)
    account = pointer.get('account', '')
    observed = b''
    if isinstance(account, str) and re.fullmatch(r'[0-9a-f]{64}', account):
        path = state_root / 'knowledge/corpus' / account / 'room-observations.json'
        if path.exists():
            if path.is_symlink() or path.parent.is_symlink() or path.stat().st_size > 2 * 1024 * 1024:
                raise RuntimeError('osk_room_observation_unsafe')
            observed = digest(path.read_bytes()).encode()
    return digest(json.dumps(pointer,sort_keys=True).encode()+code+observed)


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
        checkpoint_path = home / "sync.json"
        checkpoint = _read_json(checkpoint_path, {"managed": {}, "generation": 0})
        idx = graph.Index()
        signature = _source_signature(state_root) if source is None else ''
        if (signature and checkpoint.get('source_signature')==signature and checkpoint.get('pending')==0
            and checkpoint.get('organization', {}).get('version') == ORGANIZATION_VERSION
            and checkpoint.get('layout_pending',0)==0 and checkpoint.get('conflicts')==0 and not checkpoint.get('stale')):
            unchanged = (all(not item.get('held') and not item.get('planned_move')
                             and _same_note(home, item, _resolve_note(item, idx, contract))
                             for item in checkpoint.get('managed', {}).values() if item.get('active'))
                         and checkpoint.get('placement_signature') == _placement_signature(home, graph)
                         and checkpoint.get('active_relation_signature') == _active_relation_signature(checkpoint)
                         and _layout_pending(checkpoint, home, contract, graph) == 0)
            if unchanged:return {'ok':True,'state':'unchanged','changed':0,'pending':0,'conflicts':0,'stale':False,'source_signature':signature}
        if source is None:
            from auto_reply_knowledge_graph import collect_knowledge_graph
            source = collect_knowledge_graph(state_root / "context.sqlite3", state_root=state_root,wait_for_reindex=True,
                                             force_reindex=bool(signature and checkpoint.get('source_signature')!=signature))
        if source.get("ok") is not True:
            raise RuntimeError("osk_source_unavailable")
        from alden_osk_sources import ground_graph
        source = ground_graph(state_root, source, secrets.filter_text)
        source = json.loads(secrets.filter_text(json.dumps(source, ensure_ascii=False))[0])
        managed = checkpoint["managed"]
        for item in managed.values():
            item.setdefault('automatic_relation_block', _automatic_block(item, checkpoint, secrets))
        for item in managed.values():
            if item.get('active'):
                _reconcile_note(home, item, idx, contract)
        nodes = {str(n["id"]): n for n in source.get("nodes", []) if not str(n["id"]).startswith(("message:", "msg:"))}
        titles = {key: managed.get(key, {}).get("title") or _title(node, secrets) for key, node in nodes.items()}
        # Build incident lists once, retaining input order and one copy of a
        # self-edge. This is local to this tick; edits are re-read on every tick.
        incident = {key: [] for key in nodes}
        for edge in source.get("edges", []):
            left, right = edge.get("source"), edge.get("target")
            if isinstance(left, str) and left in incident:
                incident[left].append(edge)
            if right != left and isinstance(right, str) and right in incident:
                incident[right].append(edge)
        revisions = {}
        hub = home / "vault/00_Scope/Alden/Alden.md"
        if not hub.exists() and 'root' not in checkpoint.get('organization', {}).get('hubs', {}):
            write.create_node("Alden", "올든의 대화에서 얻은 지식과 그 출처", "올든이 관리하는 로컬 대화 지식입니다.", "agent", space="00_Scope/Alden")
        changed = 0
        conflicts = 0
        for key, node in sorted(nodes.items()):
            if _aborted(state_root):
                break
            old = managed.get(key)
            title = old["title"] if old else titles[key]
            titles[key] = title
            body = _body(node, incident[key], titles, secrets)
            revision = _revision(node, body)
            revisions[key] = revision
            space=old.get('space','00_Scope/Alden') if old else '00_Scope/Alden'
            path = home / 'vault' / space / f"{title}.md"
            if old is None and not path.exists():
                found=graph.Index().nodes.get(title)
                if found:
                    path=found[0];space=str(path.parent.relative_to(home/'vault'))
            if path.is_symlink():
                raise RuntimeError("osk_symlink_note")
            current = path.read_bytes() if path.exists() else None
            current_hash = digest(current) if current is not None else ""
            if old and (old.get('human_corrected') or current_hash != old.get("written_hash")):
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
                    refs = node.get('raw_sources') or []
                    result = write.create_node(title, summary, body, "agent", space=space, edges={'derived-from':refs} if refs else None)
                elif old:
                    history = _safe_directory(home / "history" / digest(key.encode())[:16])
                    backup = history / f"{current_hash}.md"
                    if not backup.exists():
                        backup.write_bytes(current)
                    refs = node.get('raw_sources') or []
                    previous_refs = contract.parse(path).meta.get('derived-from') or []
                    edge_changes = {'add_edges':{'derived-from':[ref for ref in refs if ref not in previous_refs]},
                                    'remove_edges':{'derived-from':[ref for ref in previous_refs if ref not in refs]}} if refs else {}
                    result = write.update_node(old['osk_id'], body=body, expect_hash=current_hash, summary=summary,
                                               **edge_changes)
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
                            "revision": revision, "active": True, "held": False, "source": node, "space": space,
                            "automatic_relation_block": _relation_block(body)}
            changed += 1
            _save(checkpoint_path, checkpoint)
        # Do not retract while the source reindex is pending or cancellation occurs.
        if not source.get("stale") and not _aborted(state_root):
            for key, item in managed.items():
                if key not in nodes:
                    item["active"] = False
        active = {key for key, item in managed.items() if item.get("active")}
        checkpoint['edges'] = [e for e in source.get('edges', []) if e.get('source') in active and e.get('target') in active]
        moved = 0
        if not _aborted(state_root):
            moved = _organize_vault(home,checkpoint,contract,graph,write,MAX_MUTATIONS-changed)
        layout_pending = _layout_pending(checkpoint, home, contract, graph)
        conflicts = max(conflicts, sum(bool(m.get('held')) for m in managed.values() if m.get('active')))
        conflicts += sum(bool(h.get('held')) for h in checkpoint.get('organization', {}).get('hubs', {}).values())
        checkpoint.update({"engine": VERSION, "commit": COMMIT, "generation": checkpoint.get("generation", 0) + 1,
                           "synced_at": int(time.time()), "source_indexed_at": source.get("indexed_at", 0),"layout_pending":layout_pending,
                           "stale": bool(source.get("stale")), "changed": changed, "moved": moved, "conflicts": conflicts,
                           "pending": sum(managed.get(k, {}).get("revision") != (revisions[k] if k in revisions else _revision(n, _body(n, incident[k], titles, secrets))) for k, n in nodes.items()),
                           "placement_signature": _placement_signature(home, graph),
                           "active_relation_signature": _active_relation_signature(checkpoint),
                           "edges": [e for e in source.get("edges", []) if e.get("source") in active and e.get("target") in active]})
        if signature:checkpoint['source_signature']=signature
        _save(checkpoint_path, checkpoint)
        return {"ok": True, 'source_signature':signature, **{k: checkpoint[k] for k in ("engine", "synced_at", "changed", "pending", "conflicts", "stale")}}
    finally:
        os.close(lock_fd)


def reorganize(state_root: Path) -> dict:
    """Placement-only maintenance, without recollecting or re-deriving Raw.

    Uses the same lock, abort policy and real SDK as the periodic producer.
    Source contents/revisions/coordinates and freshness are not rewritten.
    """
    home = _safe_directory(_home(state_root))
    lock_fd = os.open(home / 'sync.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'ok': True, 'state': 'busy'}
        if _aborted(state_root):
            return {'ok': False, 'state': 'paused'}
        checkpoint = _read_json(home / 'sync.json')
        if not checkpoint:
            return {'ok': False, 'state': 'preparing'}
        contract, graph, _, write = _load_engine(state_root)
        moved = _organize_vault(home, checkpoint, contract, graph, write, MAX_MUTATIONS)
        pending = _layout_pending(checkpoint, home, contract, graph)
        conflicts = sum(bool(m.get('held')) for m in checkpoint['managed'].values() if m.get('active'))
        conflicts += sum(bool(h.get('held')) for h in checkpoint.get('organization', {}).get('hubs', {}).values())
        checkpoint.update(layout_pending=pending, conflicts=conflicts, placement_signature=_placement_signature(home, graph))
        _save(home / 'sync.json', checkpoint)
        return {'ok': True, 'moved': moved, 'layout_pending': pending, 'conflicts': conflicts}
    finally:
        os.close(lock_fd)


def read_graph(state_root: Path, *, read_only: bool = False) -> dict:
    home = _home(state_root)
    checkpoint = _read_json(home / "sync.json")
    if not checkpoint:
        return {"ok": False, "nodes": [], "edges": [], "stale": True, "next_transition_at": 0,
                "osk": {"state": "preparing", "engine": VERSION}}
    contract, graph, secrets, _ = _load_engine(state_root, read_only=read_only)
    idx = graph.Index()
    nodes, identities, notes = [], {}, {}
    managed_by_id = {item['osk_id']: item for item in checkpoint.get('managed', {}).values()}
    hubs_by_id = {hub['osk_id']: hub for hub in checkpoint.get('organization', {}).get('hubs', {}).values()}
    rooms, _ = _context_plan(checkpoint)
    for key, item in checkpoint.get("managed", {}).items():
        if not item.get("active"):
            continue
        resolved = _resolve_note(item, idx, contract)
        if not resolved:
            continue
        path, note, _ = resolved
        label = path.stem if item.get('human_title') or path.stem != item['title'] else item['source'].get('label', path.stem)
        nodes.append({**item["source"], "description": str(note.meta.get("summary", "")), "facts": [str(note.meta.get("summary", ""))],
                      "label": label, "space": str(path.parent.relative_to(home / 'vault')), "osk_id": note.id, "is_hub": False})
        identities[path.stem] = key
        notes[path.stem] = note
    # Human-created ordinary OSK notes participate too; governance/hub never does.
    for title, (path, kind) in idx.nodes.items():
        if title in identities or kind[0] in ("governance", "workbench", "archive") or graph.is_hub(path):
            continue
        note = contract.parse(path)
        if contract.validate(note) or note.id in idx.dup_ids:
            continue
        if note.id in managed_by_id:
            continue  # A retracted imported note remains recoverable, not searchable.
        key = "osk:" + note.id
        identities[title] = key
        notes[title] = note
        nodes.append({"id": key, "label": title, "category": "memory", "description": str(note.meta.get("summary", "")),
                      "importance": 45, "updated_at": int(path.stat().st_mtime), "evidence": {"kind": "seed"},
                      "space": str(path.parent.relative_to(home / 'vault')), "osk_id": note.id, "is_hub": False})
    # Directory membership is OSK's canonical placement. Hubs and their actual
    # body Links are navigation, never invented entity-type containment edges.
    occupied = {n['space'] for n in nodes}
    for title, (path, kind) in idx.nodes.items():
        if not graph.is_hub(path) or kind[0] in ('governance', 'workbench', 'archive'):
            continue
        space = str(path.parent.relative_to(home / 'vault'))
        if not any(p == space or p.startswith(space + '/') for p in occupied):
            continue  # Preserve retired empty hubs on disk, without a fake UI cluster.
        note = contract.parse(path)
        if contract.validate(note) or note.id in idx.dup_ids:
            continue
        key = 'osk:' + note.id
        identities[title] = key
        notes[title] = note
        label = '카카오톡' if space == SOURCE_SPACE else 'Alden' if space == '00_Scope/Alden' else '맥락 · ' + title.removeprefix('대화 맥락 · ').rsplit(' · ', 1)[0]
        hub = hubs_by_id.get(note.id)
        if hub and (hub.get('human_title') or title != hub['title']):
            label = title
        elif hub and hub.get('room_ref') in rooms:
            current_label = rooms[hub['room_ref']]['source'].get('label')
            if current_label:
                label = '맥락 · ' + str(current_label)
        nodes.append({'id': key, 'label': label, 'category': 'collection', 'is_hub': True, 'space': space, 'osk_id': note.id,
                      'description': str(note.meta.get('summary', '')), 'importance': 55,
                      'updated_at': int(path.stat().st_mtime), 'evidence': {'kind': 'structure'}})
    for node in nodes:
        if node['category']!='collection': node['importance']=min(int(node.get('importance',50)),94)
    ids = {n["id"] for n in nodes}
    now = time.time()
    semantic_edges = [e for e in checkpoint.get('edges', []) if e.get('purpose') not in ('navigation', 'reference')]
    edges = [{**e, 'purpose': 'semantic'} for e in semantic_edges
             if e.get("source") in ids and e.get("target") in ids and _edge_active(e, now=now)]
    existing = {(e["source"], e["target"]) for e in edges}
    targets = dict(identities)
    for title, key in identities.items():
        targets[notes[title].id] = key
        path = idx.nodes[title][0].relative_to(home / 'vault')
        targets[path.as_posix()] = targets[path.with_suffix('').as_posix()] = key
    for title, key in identities.items():
        note = notes[title]
        from_body = replace(note, body=_reference_body(note, managed_by_id.get(note.id), checkpoint, secrets))
        for target in from_body.wikilinks():
            if target in targets and targets[target] != key and (key, targets[target]) not in existing:
                navigation = graph.is_hub(idx.nodes[title][0])
                edges.append({"source": key, "target": targets[target], "relation": "linked", "weight": 1,
                              "purpose": "navigation" if navigation else "reference", "evidence": {"kind": "structure" if navigation else "seed"}})
                existing.add((key, targets[target]))
    stale = checkpoint.get("stale", True) or int(now) - checkpoint.get("synced_at", 0) > 180
    return {"ok": True, "nodes": nodes, "edges": edges, "node_count": len(nodes), "edge_count": len(edges),
            "next_transition_at": _next_transition_at(semantic_edges, now),
            "indexed_at": checkpoint.get("source_indexed_at", 0), "stale": stale,
            "osk": {"state": "paused" if _aborted(state_root) else "ready", "engine": VERSION,
                    "synced_at": checkpoint.get("synced_at", 0), "pending": checkpoint.get("pending", 0),
                    "organization_version": checkpoint.get('organization', {}).get('version', 1),
                    "layout_pending": checkpoint.get('layout_pending', 0),
                    "conflicts": checkpoint.get("conflicts", 0)}}


def read_focus(state_root: Path, node_id: str, *, chat_id: str = '') -> dict:
    home = _home(state_root)
    checkpoint = _read_json(home / "sync.json")
    if not checkpoint:
        return {"ok": False, "facts": [], "fact_count": 0}
    contract, graph, secrets, _ = _load_engine(state_root)
    idx = graph.Index()
    item = checkpoint.get("managed", {}).get(node_id)
    resolved = _resolve_note(item, idx, contract) if item and item.get('active') else None
    if node_id.startswith("osk:"):
        resolved = _resolve_note({'osk_id': node_id[4:]}, idx, contract)
        item = next((value for value in [*checkpoint.get('managed', {}).values(),
                                        *checkpoint.get('organization', {}).get('hubs', {}).values()]
                     if value.get('osk_id') == node_id[4:]), None)
    if not resolved:
        return {"ok": False, "facts": [], "fact_count": 0}
    path, note, _ = resolved
    title = path.stem
    source = item.get('source', {}) if item and item.get('active') else {}
    evidence = source.get('evidence', {})
    human_changed = bool(item and (item.get('held') or not _same_note(home, item, resolved)))
    summary = _clean(source.get('description') if source and not human_changed else note.meta.get('summary', ''), secrets, 1200)
    summary = summary or _clean(note.meta.get('summary', ''), secrets, 1200)
    details = {
        'node_id': node_id, 'summary': summary, 'title': title,
        'space': str(path.parent.relative_to(home / 'vault')), 'osk_id': note.id,
        'kind': str(source.get('category') or ('collection' if graph.is_hub(idx.nodes[title][0]) else 'memory')),
        'basis': 'structure' if graph.is_hub(idx.nodes[title][0]) else 'snapshot' if evidence.get('kind') in ('local_db_snapshot', 'snapshot') else 'ledger' if evidence.get('kind') in ('decision_ledger', 'ledger') else 'note',
        'key_facts': [_clean(value, secrets, 600) for value in source.get('facts', [])[:6] if isinstance(value, str)] if not human_changed else [],
        'source_updated_at': source.get('updated_at', 0),
        'note_updated_at': int(idx.nodes[title][0].stat().st_mtime),
        'scope_room_id': '',
    }
    actor=re.fullmatch(r'person:kakao:([0-9a-f]{64}):actor:([0-9]+)',node_id)
    room=re.fullmatch(r'chat:kakao:([0-9a-f]{64}):room:([0-9]+)',node_id)
    if actor or room:
        from alden_corpus import search, room_displays
        scope=actor or room
        selected_room = str(chat_id or '').strip()
        if selected_room.startswith('kakao:'):
            parsed = re.fullmatch(r'kakao:([0-9a-f]{64}):room:([0-9]+)', selected_room)
            if not parsed or parsed[1] != scope[1]:
                return {'ok': False, 'reason': 'focus_room_scope_invalid', 'facts': [], 'fact_count': 0}
            selected_room = parsed[2]
        if selected_room and (not selected_room.isascii() or not selected_room.isdigit() or not 0 < int(selected_room) < 2**63
                              or room and selected_room != room[2]):
            return {'ok': False, 'reason': 'focus_room_scope_invalid', 'facts': [], 'fact_count': 0}
        selected_room = room[2] if room else selected_room
        details['scope_room_id'] = selected_room
        details['key_facts'] = []
        if actor and selected_room and not human_changed:
            summary = '선택한 채팅방의 원문에서 발화가 확인된 카카오톡 대화 상대입니다.'
            details['summary'] = summary
        result=search(state_root,'',author_id=actor[2] if actor else '',chat_id=selected_room,expected_account=scope[1])
        if result.get('ok'):
            # A selected room cannot inherit an actor's multi-room cached samples.
            details['key_facts'] = []
            titles = {key.rsplit(':', 1)[-1]: value['source'].get('label', '') for key, value in checkpoint.get('managed', {}).items()
                      if key.startswith('chat:kakao:' + scope[1] + ':room:') and value.get('active')}
            room_ids = list(dict.fromkeys(str(row['chat_id']) for row in result['items']))
            try:
                displays = room_displays(state_root, room_ids, expected_account=scope[1])
            except RuntimeError as error:
                if 'account' in str(error) or 'identity' in str(error):
                    return {'ok': False, 'reason': 'focus_room_scope_invalid', 'facts': [], 'fact_count': 0}
                displays = {}  # Older/incomplete metadata cannot attest a current title.
            sources, seen = [], set()
            for raw in result['items']:
                if raw['source_id'] in seen: continue
                seen.add(raw['source_id'])
                row = dict(raw)
                row['content'] = _clean(row['content'], secrets, 1000)
                row['sender'] = _clean(row['sender'], secrets, 128)
                display = displays.get(str(row['chat_id']), {})
                row['room_title'] = _clean(display.get('label') or titles.get(str(row['chat_id'])) or '', secrets, 128)
                row['room_title_source'] = display.get('label_source') or ('saved_graph' if row['room_title'] else 'unavailable')
                sources.append(row)
            if actor and selected_room and not sources and not human_changed:
                summary = '선택한 채팅방에서 이 인물의 원문을 아직 찾지 못했습니다.'
                details['summary'] = summary
            facts=[summary]+[str(row['date'])[:10]+' · '+str(row['sender'])+' · '+str(row['content']) for row in sources]
            details['basis'] = 'note' if human_changed else 'snapshot'
            details['sample_count'] = len(sources)
            return {'ok':True,'facts':facts,'fact_count':len(facts),'focus_node_id':node_id,'sources':sources,'details':details,'search_mode':'bm25'}
        details['source_unavailable'] = True
        if actor and selected_room and not human_changed:
            summary = '선택한 채팅방의 원문에 접근할 수 없어 이 인물의 세부 내용을 확인하지 못했습니다.'
            details['summary'] = summary
        return {'ok': True, 'facts': [summary], 'fact_count': 1, 'focus_node_id': node_id, 'sources': [], 'details': details}
    return {"ok": True, "facts": [summary, _reference_body(note, item, checkpoint, secrets)[:1200]], "fact_count": 2, "focus_node_id": node_id, 'details': details}


def capture_room_observations(state_root: Path, binary: Path, *, collect, now: float) -> dict:
    """Publish bounded names attested by the same live DB listing.

    Failed/foreign listings leave known names intact. There is no raw UID/UUID,
    message body, AX/name guess, model, new worker or original DB mutation.
    """
    pointer_path = state_root / 'knowledge/corpus/current.json'
    pointer = _read_json(pointer_path)
    account, snapshot = pointer.get('account', ''), pointer.get('snapshot')
    if not isinstance(account, str) or not re.fullmatch(r'[0-9a-f]{64}', account) or not isinstance(snapshot, str) or not snapshot:
        return {'state': 'unavailable', 'rooms': 0}
    folder = pointer_path.parent / account
    target = folder / 'room-observations.json'
    receipt_path = _home(state_root) / 'room-observation-producer.json'
    previous = _read_json(receipt_path)
    if previous.get('retry_at', 0) > now:
        return {'state': 'backoff', 'rooms': 0}
    try:
        value = collect(binary, ['local-chats', '-n', '10000', '--with-source'])
        if value.get('schema_version') != 1 or value.get('account_fingerprint') != account:
            raise RuntimeError('room_observation_source_account_mismatch')
        at, rows = value.get('observed_at'), value.get('rooms')
        if (type(at) not in (int, float) or not isfinite(at) or not -120 <= now - at <= 120
                or not isinstance(rows, list) or len(rows) > 10000):
            raise RuntimeError('room_observation_source_invalid')
        observed, conflicts = {}, set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            number = str(row.get('chat_id', ''))
            if not re.fullmatch(r'[1-9][0-9]{0,18}', number) or not 0 < int(number) < 2**63:
                continue
            kind = row.get('label_kind')
            if kind == 'room_title':
                label, basis = row.get('room_title'), 'room_title'
            elif kind in ('default_display_name', 'participant_alias'):
                label, basis = row.get('display_name') or row.get('chat_name'), 'display_name'
            else:
                continue
            if not isinstance(label, str) or not label.strip() or len(label) > 512:
                continue
            entry = {'chat_id': number, 'label': label, 'label_kind': basis, 'source': 'local_chats', 'observed_at': at}
            name_basis = row.get('name_basis')
            if isinstance(name_basis, str) and len(name_basis) <= 96:
                entry['name_basis'] = name_basis
            if number in observed and observed[number] != entry:
                conflicts.add(number)
            observed[number] = entry
        old = _read_json(target)
        if old and old.get('account') != account:
            raise RuntimeError('room_observation_cache_account_mismatch')
        if old.get('schema_version') == 1 and isinstance(old.get('rooms'), list) and len(old['rooms']) <= 10000:
            for row in old['rooms']:
                if not isinstance(row, dict):
                    continue
                number = row.get('chat_id')
                old_at = row.get('observed_at', old.get('observed_at'))
                if (number in observed or number in conflicts or not isinstance(number, str)
                        or not re.fullmatch(r'[1-9][0-9]{0,18}', number) or not 0 < int(number) < 2**63
                        or type(old_at) not in (int, float) or not isfinite(old_at) or not -60 <= now - old_at <= 86400
                        or row.get('source') not in ('local_history_rooms', 'local_chats', 'ax')
                        or row.get('label_kind') not in ('room_title', 'display_name')
                        or not isinstance(row.get('label'), str) or not row['label'].strip() or len(row['label']) > 512):
                    continue
                observed[number] = {**row, 'observed_at': old_at}
        rows = [row for number, row in observed.items() if number not in conflicts]
        if not rows or pointer != _read_json(pointer_path) or folder.is_symlink():
            raise RuntimeError('room_observation_publication_changed')
        with sqlite3.connect((folder / 'context.sqlite3').as_uri() + '?mode=ro', uri=True, timeout=.2) as db:
            db.execute('PRAGMA query_only=ON')
            meta = dict(db.execute("SELECT key,value FROM corpus_meta WHERE key IN ('account','snapshot','complete')"))
            if meta != {'account': account, 'snapshot': snapshot, 'complete': '1'}:
                raise RuntimeError('room_observation_corpus_not_ready')
        payload = {'schema_version': 1, 'account': account, 'corpus_snapshot': snapshot, 'observed_at': at, 'rooms': rows}
        if len(json.dumps(payload, ensure_ascii=False).encode()) > 2 * 1024 * 1024:
            raise RuntimeError('room_observation_too_large')
        _save(target, payload)
        if _read_json(target) != payload:
            raise RuntimeError('room_observation_readback_failed')
        result = {'state': 'ready', 'rooms': len(rows), 'observed_at': at, 'retry_at': 0}
    except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as error:
        result = {'state': 'unavailable', 'rooms': 0, 'reason': type(error).__name__, 'retry_at': now + 300}
    _save(receipt_path, result)
    return result


def sync_cycle(state_root: Path, binary: Path | None = None, *, collect=None, sync=None, record=None, now=None, cycle=None) -> dict:
    """Collection readiness does not prevent updating a valid saved corpus.

    A waiting original-data permission is reported independently. Retry only
    after the bounded backoff; no send/worker/model action is part of this cycle.
    """
    from alden_history import cycle_step, _local_cli, LocalDataAccessWaiting
    from auto_reply_knowledge_graph import _index_db_path
    collect = collect or _local_cli; sync = sync or synchronize; record = record or cycle_step
    now = time.time() if now is None else now
    cycle = cycle or uuid.uuid4().hex; status_path = _home(state_root)/'producer.json'
    previous = _read_json(status_path); collection = 'not_requested'; corpus = {}
    _safe_directory(_home(state_root))
    if _aborted(state_root):
        record(state_root,cycle,'paused');return {'ok':False,'state':'paused'}
    waiting = binary is not None and previous.get('collection')=='waiting' and now < previous.get('collection_retry_at',0)
    if binary and not waiting:
        record(state_root,cycle,'collecting')
        try:
            collected=collect(binary,['local-db-collect','--index-dir',str(state_root/'knowledge/corpus'),'--max-rows','500000','--abort-state',str(state_root/'alden-abort.json')])
            corpus=collected.get('index',{});collection='complete' if corpus.get('complete') else 'pending'
        except LocalDataAccessWaiting:
            collection='waiting'
    elif waiting: collection='waiting'
    # Fail closed if no valid saved account corpus exists. This does not probe
    # the original Kakao database or change its permission policy.
    source_path = _index_db_path(state_root)
    if not source_path.is_file():raise RuntimeError('saved_corpus_unavailable')
    names = capture_room_observations(state_root, binary, collect=collect, now=now) if binary and collection == 'complete' else {'state': 'not_requested', 'rooms': 0}
    record(state_root,cycle,'graphing',reason='LocalDataAccessWaiting' if collection=='waiting' else '')
    if ( _home(state_root)/'raw-sources.json').is_file():
        from alden_osk_delta import capture
        _contract,_graph,secrets,_write=_load_engine(state_root)
        archive=capture(state_root,secrets.filter_text,cancelled=lambda:_aborted(state_root))
    else: archive={}
    result=sync(state_root)
    checkpoint=_read_json(_home(state_root)/'sync.json')
    pending=int(corpus.get('pending_messages',0))+int(result.get('pending',0))+int(checkpoint.get('layout_pending',0))+int(result.get('conflicts',0))
    phase='paused' if _aborted(state_root) else 'complete' if collection!='waiting' and result.get('ok') and result.get('state')!='busy' and not result.get('stale') and pending==0 else 'pending'
    record(state_root,cycle,phase,nodes=sum(bool(m.get('active')) for m in checkpoint.get('managed',{}).values()),pending=pending,changed=result.get('changed',0),reason='LocalDataAccessWaiting' if collection=='waiting' else '')
    status={'schema_version':1,'checked_at':int(now),'collection':collection,'collection_retry_at':int(now+300) if collection=='waiting' and not waiting else previous.get('collection_retry_at',0) if waiting else 0,
            'saved_corpus_ready':bool(result.get('ok') and not result.get('stale') and pending==0),'source_signature':result.get('source_signature'),
            'room_name_observation':names,
            'archive_snapshot':archive.get('snapshot'),'archive_rows':archive.get('rows')}
    _save(status_path,status)
    return {**result,'collection':collection,'archive':archive,'room_name_observation':names}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument('--read-only', action='store_true', help='Read the existing SDK vault without bootstrap or synchronization.')
    parser.add_argument("--sync", action="store_true")
    parser.add_argument('--bin',type=Path)
    args = parser.parse_args()
    cycle=uuid.uuid4().hex
    try:
        if args.read_only and args.sync:
            raise RuntimeError('osk_read_only_sync_refused')
        result = sync_cycle(args.state_root,args.bin,cycle=cycle) if args.sync else read_graph(args.state_root, read_only=args.read_only)
    except Exception as error:
        result = {"ok": False, "state": "unavailable", "error": type(error).__name__}
        if args.sync and args.bin:
            from alden_history import cycle_step
            cycle_step(args.state_root,cycle,'failed',reason=type(error).__name__)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
