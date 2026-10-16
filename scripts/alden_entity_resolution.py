"""Explicit, reversible CollectionStore v3 duplicate-resolution overlays.

The source journal, memberships, versions, original records and directed edges
remain authoritative. A resolution is a user-confirmed *display decision*, not
proof that matching text or labels establish semantic identity. No model or
external provider is involved.
"""
from __future__ import annotations

from contextlib import closing, contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import sqlite3
import time
import uuid

from alden_collection import CollectionStore, MAX_RECORD_BYTES, digest, encoded, identity, require_relation_binding
from alden_content_evidence import POLICY as CONTENT_POLICY, content_evidence_class, declared_source_annotations as _declared_source_annotations
from alden_graph_quality import _json


FEATURE_VERSION = 1
PLAN_SCHEMA = 1
MAX_MEMBERS = 16
MAX_RELATIONS = 512
MAX_HISTORY = 100
MAX_PLAN_BYTES = 32768
MAX_ACTIVE_GROUPS = 256
BACKUP_SECONDS = 10
SOURCE_POLICY = "collection-resolution-source-v2"
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_PLAN_KEYS = frozenset({"schema", "project", "members", "canonical", "reason",
                        "revision", "source_fingerprint", "plan_sha256"})


def _options(query):
    if query is None:
        return {}
    if isinstance(query, str):
        if len(query.encode("utf-8")) > 262144:
            raise ValueError("collection_resolution_query_too_large")
        try:
            query = json.loads(query)
        except (ValueError, TypeError) as error:
            raise ValueError("collection_resolution_query_invalid") from error
    if not isinstance(query, dict):
        raise ValueError("collection_resolution_query_invalid")
    return query


def _text(value, field, maximum=256):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError("collection_resolution_" + field + "_invalid")
    return value


def _project(options):
    projects = options.get("projects")
    if not isinstance(projects, list) or len(projects) != 1:
        raise ValueError("collection_resolution_project_scope_invalid")
    return _text(projects[0], "project", 128)


def _member_shape(value):
    if not isinstance(value, dict) or set(value) != {"target_id", "document_id", "version"}:
        raise ValueError("collection_resolution_member_invalid")
    return {key: _text(value.get(key), key) for key in ("target_id", "document_id", "version")}


def _members(value):
    if not isinstance(value, list) or not 2 <= len(value) <= MAX_MEMBERS:
        raise ValueError("collection_resolution_members_budget_invalid")
    members = sorted((_member_shape(item) for item in value),
                     key=lambda item: (item["document_id"], item["target_id"]))
    # A document already has a stable original identity in CollectionStore.
    # Resolving two memberships for that same ID would create an ambiguous map.
    ids = [item["document_id"] for item in members]
    if len(set(ids)) != len(ids):
        raise ValueError("collection_resolution_distinct_documents_required")
    return members


def _canonical(value, members):
    if not isinstance(value, dict) or set(value) != {"target_id", "document_id"}:
        raise ValueError("collection_resolution_canonical_invalid")
    result = {key: _text(value.get(key), key) for key in ("target_id", "document_id")}
    if not any(all(item[key] == result[key] for key in result) for item in members):
        raise ValueError("collection_resolution_canonical_not_member")
    return result


def _reason(value):
    return _text(value, "reason", 1024)


def _request_id(value):
    return _text(value, "request_id", 256)


def _sha(value, name):
    if not isinstance(value, str) or _HEX.fullmatch(value) is None:
        raise ValueError("collection_resolution_" + name + "_invalid")
    return value


def _checked_plan(value):
    if not isinstance(value, dict):
        raise ValueError("collection_resolution_plan_invalid")
    try:
        size = len(encoded(value))
    except (ValueError, TypeError) as error:
        raise ValueError("collection_resolution_plan_invalid") from error
    if size > MAX_PLAN_BYTES:
        raise ValueError("collection_resolution_plan_too_large")
    if set(value) != _PLAN_KEYS:
        raise ValueError("collection_resolution_plan_invalid")
    if type(value["schema"]) is not int or value["schema"] != PLAN_SCHEMA:
        raise ValueError("collection_resolution_plan_schema_invalid")
    project = _text(value["project"], "project", 128)
    members = _members(value["members"])
    if members != value["members"]:
        raise ValueError("collection_resolution_plan_order_invalid")
    canonical = _canonical(value["canonical"], members)
    reason = _reason(value["reason"])
    revision = value["revision"]
    if type(revision) is not int or revision < 0:
        raise ValueError("collection_resolution_revision_invalid")
    source_hash = _sha(value["source_fingerprint"], "source_fingerprint")
    given_hash = _sha(value["plan_sha256"], "plan_sha256")
    body = {"schema": PLAN_SCHEMA, "project": project, "members": members,
            "canonical": canonical, "reason": reason, "revision": revision,
            "source_fingerprint": source_hash}
    if digest(encoded(body)) != given_hash:
        raise ValueError("collection_resolution_plan_hash_invalid")
    return {**body, "plan_sha256": given_hash}


def _has_feature(db):
    tables = {row[0] for row in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'resolution_%'")}
    if not tables:
        return False
    expected = {"resolution_feature_meta", "resolution_revisions", "resolution_decisions",
                "resolution_active_groups", "resolution_active_members", "resolution_events",
                "resolution_requests"}
    if not expected.issubset(tables):
        raise RuntimeError("collection_resolution_schema_incomplete")
    version = db.execute("SELECT feature_version FROM resolution_feature_meta WHERE name='entity-resolution'").fetchone()
    if version is None or version[0] != FEATURE_VERSION:
        raise RuntimeError("collection_resolution_schema_version_unsupported")
    return True


def _revision(db, project):
    if not _has_feature(db):
        return 0
    row = db.execute("SELECT revision FROM resolution_revisions WHERE project=?", (project,)).fetchone()
    return row[0] if row else 0


def _project_allowed(db, project):
    return db.execute("""SELECT 1 FROM target_projects WHERE project=? AND permission!='denied' LIMIT 1""",
                      (project,)).fetchone() is not None


def _require_project(db, project):
    if not _project_allowed(db, project):
        raise ValueError("collection_resolution_project_scope_denied")


def _member_permissions(db, project, members):
    return all(db.execute("""SELECT 1 FROM target_projects WHERE target_id=? AND project=?
                            AND permission!='denied'""", (member["target_id"], project)).fetchone()
               for member in members)


def _source_rows(db, store, project, members, *, read_blobs, show_records, include_annotations=False):
    if include_annotations and not read_blobs:
        raise ValueError('collection_resolution_annotations_require_verified_source')
    fingerprint_rows, records = [], []
    for member in members:
        row = db.execute("""SELECT m.current_version,m.availability AS membership_availability,
              d.availability AS document_availability,d.platform,d.original_id,
              v.document_id AS version_document_id,v.raw_sha256,v.raw_path,v.label,v.body,
              v.metadata,v.collected_at,p.permission
              FROM memberships m JOIN documents d ON d.id=m.document_id
              LEFT JOIN versions v ON v.id=m.current_version
              LEFT JOIN target_projects p ON p.target_id=m.target_id AND p.project=?
              WHERE m.target_id=? AND m.document_id=?""",
              (project, member["target_id"], member["document_id"])).fetchone()
        if row is None:
            raise ValueError("collection_resolution_membership_missing")
        if row["permission"] is None or row["permission"] == "denied":
            raise ValueError("collection_resolution_member_permission_revoked")
        if row["membership_availability"] != "available" or row["document_availability"] != "available":
            raise ValueError("collection_resolution_member_unavailable")
        if row["current_version"] != member["version"]:
            raise ValueError("collection_resolution_member_version_changed")
        if row["version_document_id"] != member["document_id"]:
            raise RuntimeError("collection_resolution_version_integrity")
        try:
            original_identity = identity(row["platform"], row["original_id"])
        except (TypeError, ValueError) as error:
            raise RuntimeError("collection_resolution_original_identity_invalid") from error
        if original_identity != member["document_id"]:
            raise RuntimeError("collection_resolution_original_identity_mismatch")
        source = None
        if read_blobs:
            raw = CollectionStore._verified_source_blob(store.blobs, row["raw_path"], MAX_RECORD_BYTES)
            if digest(raw) != row["raw_sha256"]:
                raise RuntimeError("collection_resolution_source_integrity")
            try:
                source = _json(raw)
            except (ValueError, TypeError, RecursionError) as error:
                raise RuntimeError("collection_resolution_source_integrity") from error
        try:
            metadata = _json(row["metadata"])
        except (TypeError, ValueError, RecursionError) as error:
            raise RuntimeError("collection_resolution_metadata_integrity") from error
        if not isinstance(metadata, dict):
            raise RuntimeError("collection_resolution_metadata_integrity")
        fingerprint_rows.append({
            **member, "permission": row["permission"], "platform": row["platform"],
            "original_id": row["original_id"], "raw_sha256": row["raw_sha256"],
            "raw_path": row["raw_path"], "label_sha256": digest(row["label"].encode()),
            "body_sha256": digest(row["body"].encode()),
            "metadata_sha256": digest(row["metadata"].encode()),
            "collected_at": row["collected_at"]})
        if show_records:
            body = row["body"]
            body_source = "stored_body"
            if (row["platform"] == "graph" and isinstance(source, dict)
                    and isinstance(source.get("localOriginalText"), str)
                    and source["localOriginalText"].strip()):
                body = source["localOriginalText"]
                body_source = "retained_record.localOriginalText"
            records.append({
                **member, "platform": row["platform"], "original_id": row["original_id"],
                "label": row["label"], "body": body[:12000], "body_source": body_source,
                "truncated": len(body) > 12000,
                "collected_at": row["collected_at"], "raw_sha256": row["raw_sha256"],
                "source_url": metadata.get("source_url"),
                "basis": "permitted_current_stored_record",
                "content_evidence": {"policy": CONTENT_POLICY,
                    "classification": content_evidence_class(metadata, body, source=source,
                        body_source=body_source)}})
            if include_annotations:
                records[-1]['source_annotations'] = _declared_source_annotations(source)
    return digest(encoded(fingerprint_rows)), records


def _relations(db, project, members, *, show_relations):
    ids = [m["document_id"] for m in members]
    marks = ",".join("?" for _ in ids)
    # Each edge belongs to its original target, retains its original direction,
    # and must have two currently permitted, available project endpoints.
    rows = db.execute("""SELECT r.id,r.target_id,r.source,r.target,r.type,r.version,r.evidence
       FROM relations r WHERE r.active=1 AND
       (r.source IN (""" + marks + ") OR r.target IN (" + marks + "))" + """
       AND EXISTS(SELECT 1 FROM target_projects p WHERE p.target_id=r.target_id
                  AND p.project=? AND p.permission!='denied')
       AND EXISTS(SELECT 1 FROM memberships m JOIN documents d ON d.id=m.document_id
                  JOIN versions v ON v.id=m.current_version AND v.document_id=m.document_id
                  JOIN target_projects p ON p.target_id=m.target_id
                  WHERE m.document_id=r.source AND m.availability='available'
                    AND d.availability='available' AND p.project=? AND p.permission!='denied')
       AND EXISTS(SELECT 1 FROM memberships m JOIN documents d ON d.id=m.document_id
                  JOIN versions v ON v.id=m.current_version AND v.document_id=m.document_id
                  JOIN target_projects p ON p.target_id=m.target_id
                  WHERE m.document_id=r.target AND m.availability='available'
                    AND d.availability='available' AND p.project=? AND p.permission!='denied')
       ORDER BY r.id LIMIT ?""", (*ids, *ids, project, project, project, MAX_RELATIONS + 1)).fetchall()
    if len(rows) > MAX_RELATIONS:
        raise ValueError("collection_resolution_relations_budget_exceeded")
    fingerprints, readable = [], []
    for row in rows:
        item = dict(row)
        if len(item["evidence"].encode()) > 16384:
            raise ValueError("collection_resolution_relation_evidence_budget_exceeded")
        try:
            require_relation_binding(db, item)
        except RuntimeError as error:
            raise RuntimeError("collection_resolution_relation_integrity") from error
        fingerprints.append(item)
        if show_relations:
            try:
                evidence = json.loads(item["evidence"])
            except (ValueError, TypeError) as error:
                raise RuntimeError("collection_resolution_relation_integrity") from error
            readable.append({**item, "evidence": evidence})
    return digest(encoded(fingerprints)), readable


def _collection_fingerprint(db, members):
    journal = db.execute("SELECT value FROM meta WHERE key='journal_id'").fetchone()
    if (journal is None or not isinstance(journal[0], str)
            or re.fullmatch(r"[0-9a-f]{32}", journal[0]) is None):
        raise RuntimeError("collection_resolution_journal_identity_invalid")
    events = []
    for member in members:
        # Indexed per-document/stage keyset reads. Indexing or unrelated
        # documents never gain authority to invalidate a reviewed source.
        event = db.execute("""SELECT sequence,event_id,run_id,target_id,stage,at,
                              document_id,version,details FROM events
                              WHERE document_id=? AND target_id=? AND stage='stored'
                                AND version=? ORDER BY sequence DESC LIMIT 1""",
                           (member["document_id"], member["target_id"], member["version"])).fetchone()
        if event is None:
            raise RuntimeError("collection_resolution_source_event_missing")
        events.append(dict(event))
    return digest(encoded([SOURCE_POLICY, journal[0], events]))


def _evidence(db, store, project, members, *, read_blobs=False, show=False):
    members_hash, records = _source_rows(db, store, project, members,
                                         read_blobs=read_blobs, show_records=show)
    relations_hash, relations = _relations(db, project, members, show_relations=show)
    return {"member_fingerprint": members_hash, "relations_fingerprint": relations_hash,
            "source_fingerprint": digest(encoded([
                SOURCE_POLICY, members_hash, relations_hash, _collection_fingerprint(db, members)])),
            "records": records, "relations": relations}


def _validity(db, store, decision):
    members = json.loads(decision["members_json"])
    project = decision["project"]
    try:
        now = _evidence(db, store, project, members)
    except (ValueError, RuntimeError) as error:
        return {"valid": False, "state": str(error)}
    if now["member_fingerprint"] != decision["member_fingerprint"]:
        return {"valid": False, "state": "collection_resolution_source_changed"}
    if now["relations_fingerprint"] != decision["relations_fingerprint"]:
        return {"valid": False, "state": "collection_resolution_relations_changed"}
    if now["source_fingerprint"] != decision["source_fingerprint"]:
        return {"valid": False, "state": "collection_resolution_source_provenance_changed"}
    return {"valid": True, "state": "current"}


def _new_feature_tables(db):
    if _has_feature(db):
        return
    # These tables form a single optional, feature-versioned transaction.
    # Original collection tables and collection journal are never altered.
    statements = """
      CREATE TABLE resolution_feature_meta(name TEXT PRIMARY KEY,feature_version INTEGER NOT NULL);
      INSERT INTO resolution_feature_meta VALUES('entity-resolution',1);
      CREATE TABLE resolution_revisions(project TEXT PRIMARY KEY,revision INTEGER NOT NULL CHECK(revision>=0));
      CREATE TABLE resolution_decisions(
        resolution_id TEXT PRIMARY KEY,project TEXT NOT NULL,members_json TEXT NOT NULL,
        canonical_json TEXT NOT NULL,reason TEXT NOT NULL,plan_sha256 TEXT NOT NULL,
        source_fingerprint TEXT NOT NULL,member_fingerprint TEXT NOT NULL,
        relations_fingerprint TEXT NOT NULL,applied_revision INTEGER NOT NULL,
        created_at REAL NOT NULL);
      CREATE TABLE resolution_active_groups(
        project TEXT NOT NULL,resolution_id TEXT NOT NULL,PRIMARY KEY(project,resolution_id),
        FOREIGN KEY(resolution_id) REFERENCES resolution_decisions(resolution_id));
      CREATE TABLE resolution_active_members(
        project TEXT NOT NULL,document_id TEXT NOT NULL,target_id TEXT NOT NULL,
        version TEXT NOT NULL,resolution_id TEXT NOT NULL,
        PRIMARY KEY(project,document_id),
        FOREIGN KEY(project,resolution_id) REFERENCES resolution_active_groups(project,resolution_id)
            ON DELETE CASCADE);
      CREATE INDEX resolution_active_by_id ON resolution_active_members(resolution_id);
      CREATE TABLE resolution_events(
        sequence INTEGER PRIMARY KEY AUTOINCREMENT,resolution_id TEXT NOT NULL,
        project TEXT NOT NULL,action TEXT NOT NULL CHECK(action IN ('apply','revert')),
        request_id TEXT NOT NULL UNIQUE,revision INTEGER NOT NULL,at REAL NOT NULL,
        FOREIGN KEY(resolution_id) REFERENCES resolution_decisions(resolution_id));
      CREATE TABLE resolution_requests(
        request_id TEXT PRIMARY KEY,project TEXT NOT NULL,action TEXT NOT NULL,
        payload_sha256 TEXT NOT NULL,resolution_id TEXT NOT NULL,result_json TEXT NOT NULL,
        FOREIGN KEY(resolution_id) REFERENCES resolution_decisions(resolution_id));
    """
    # sqlite3.executescript() implicitly COMMITs an existing transaction.
    # Individual statements are essential for atomic schema+decision writes.
    for statement in statements.split(";"):
        if statement.strip():
            db.execute(statement)


@contextmanager
def _mutation_lock(store):
    # Serializes optional first-time bootstrap among resolution writers only.
    # CollectionStore ingest remains serialized by SQLite's writer lock.
    path = store.locks / "entity-resolution-feature.lock"
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _backup_before_feature(store):
    started = time.monotonic()
    def check(*_progress):
        if time.monotonic() - started >= BACKUP_SECONDS:
            raise RuntimeError("collection_resolution_backup_time_budget")
    check()
    path = store.root / ("collection.resolution-before-v1-" + uuid.uuid4().hex + ".sqlite3")
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    os.close(fd)
    try:
        with closing(sqlite3.connect(store.path.as_uri() + "?mode=ro", uri=True, timeout=5)) as source:
            with closing(sqlite3.connect(path, timeout=5)) as copy:
                source.backup(copy, pages=128, progress=check, sleep=.01)
                check()  # SQLite-consistent snapshot, including WAL.
                copy.set_progress_handler(lambda: int(time.monotonic() - started >= BACKUP_SECONDS), 1000)
                if copy.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError("collection_resolution_backup_integrity")
                check()
                row = copy.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
                if row is None or row[0] != "3":
                    raise RuntimeError("collection_resolution_backup_schema_invalid")
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
            check()
        finally:
            os.close(fd)
    except BaseException as error:
        path.unlink(missing_ok=True)
        if isinstance(error, sqlite3.Error) and time.monotonic() - started >= BACKUP_SECONDS:
            raise RuntimeError("collection_resolution_backup_time_budget") from error
        raise


@contextmanager
def _write_db(store):
    db = sqlite3.connect(store.path, timeout=5)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    try:
        db.execute("BEGIN IMMEDIATE")
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def _request_result(db, project, request_id, action, payload_hash):
    if not _has_feature(db):
        return None
    saved = db.execute("SELECT * FROM resolution_requests WHERE request_id=?", (request_id,)).fetchone()
    if saved is None:
        return None
    if (saved["project"] != project or saved["action"] != action or
            saved["payload_sha256"] != payload_hash):
        raise ValueError("collection_resolution_request_id_conflict")
    decision = db.execute("SELECT members_json FROM resolution_decisions WHERE resolution_id=?",
                          (saved["resolution_id"],)).fetchone()
    if decision is None or not _member_permissions(db, project, json.loads(decision[0])):
        raise ValueError("collection_resolution_request_scope_denied")
    # EXACT original result: a previous apply remains recorded as successful
    # even if a later, distinct request has reverted the active overlay.
    return json.loads(saved["result_json"])


def _check_plan_current(db, store, plan, *, read_blobs):
    _require_project(db, plan["project"])
    if _revision(db, plan["project"]) != plan["revision"]:
        raise ValueError("collection_resolution_revision_changed")
    evidence = _evidence(db, store, plan["project"], plan["members"], read_blobs=read_blobs)
    if evidence["source_fingerprint"] != plan["source_fingerprint"]:
        raise ValueError("collection_resolution_source_fingerprint_changed")
    if _has_feature(db):
        members = [m["document_id"] for m in plan["members"]]
        marks = ",".join("?" for _ in members)
        clash = db.execute("SELECT resolution_id FROM resolution_active_members WHERE project=? AND document_id IN (" +
                           marks + ") LIMIT 1", (plan["project"], *members)).fetchone()
        if clash:
            raise ValueError("collection_resolution_overlapping_active_group")
        groups = db.execute("SELECT resolution_id FROM resolution_active_groups WHERE project=? LIMIT ?",
                            (plan["project"], MAX_ACTIVE_GROUPS + 1)).fetchall()
        if len(groups) >= MAX_ACTIVE_GROUPS:
            raise ValueError("collection_resolution_active_group_budget_exceeded")
    return evidence


def _apply(store, plan, request_id):
    project = plan["project"]
    payload_hash = digest(encoded({"action": "apply", "plan": plan}))
    with _mutation_lock(store):
        # Validate read-only before the optional backup and schema bootstrap.
        with store.database() as db:
            _require_project(db, project)
            replay = _request_result(db, project, request_id, "apply", payload_hash)
            if replay is not None:
                return replay
            _check_plan_current(db, store, plan, read_blobs=True)
            missing = not _has_feature(db)
        if missing:
            _backup_before_feature(store)
        with _write_db(store) as db:
            _require_project(db, project)
            replay = _request_result(db, project, request_id, "apply", payload_hash)
            if replay is not None:
                return replay
            evidence = _check_plan_current(db, store, plan, read_blobs=True)
            if not _has_feature(db):
                _new_feature_tables(db)
            new_revision = plan["revision"] + 1
            resolution_id = "resolution:" + digest(encoded([project, request_id, plan["plan_sha256"]]))
            now = time.time()
            db.execute("""INSERT INTO resolution_decisions VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (
                resolution_id, project, encoded(plan["members"]).decode(),
                encoded(plan["canonical"]).decode(), plan["reason"], plan["plan_sha256"],
                plan["source_fingerprint"], evidence["member_fingerprint"],
                evidence["relations_fingerprint"], new_revision, now))
            db.execute("INSERT INTO resolution_active_groups VALUES(?,?)", (project, resolution_id))
            db.executemany("INSERT INTO resolution_active_members VALUES(?,?,?,?,?)", [
                (project, m["document_id"], m["target_id"], m["version"], resolution_id)
                for m in plan["members"]])
            db.execute("INSERT INTO resolution_events(resolution_id,project,action,request_id,revision,at) VALUES(?,?,?,?,?,?)",
                       (resolution_id, project, "apply", request_id, new_revision, now))
            db.execute("INSERT INTO resolution_revisions VALUES(?,?) ON CONFLICT(project) DO UPDATE SET revision=excluded.revision",
                       (project, new_revision))
            result = {"ok": True, "project": project, "resolution_id": resolution_id,
                      "state": "active", "revision": new_revision,
                      "members": plan["members"], "canonical": plan["canonical"],
                      "reason": plan["reason"], "plan_sha256": plan["plan_sha256"],
                      "source_fingerprint": plan["source_fingerprint"]}
            db.execute("INSERT INTO resolution_requests VALUES(?,?,?,?,?,?)",
                       (request_id, project, "apply", payload_hash, resolution_id, encoded(result).decode()))
            return result


def _revert(store, project, resolution_id, expected_revision, request_id):
    payload_hash = digest(encoded({"action": "revert", "project": project,
                                   "resolution_id": resolution_id, "expected_revision": expected_revision}))
    with _mutation_lock(store), _write_db(store) as db:
        _require_project(db, project)
        if not _has_feature(db):
            raise ValueError("collection_resolution_not_found")
        replay = _request_result(db, project, request_id, "revert", payload_hash)
        if replay is not None:
            return replay
        decision = db.execute("SELECT 1 FROM resolution_decisions WHERE resolution_id=? AND project=?",
                              (resolution_id, project)).fetchone()
        if decision is None:
            raise ValueError("collection_resolution_not_found_in_project")
        if _revision(db, project) != expected_revision:
            raise ValueError("collection_resolution_revision_changed")
        active = db.execute("SELECT 1 FROM resolution_active_groups WHERE project=? AND resolution_id=?",
                            (project, resolution_id)).fetchone()
        if active is None:
            raise ValueError("collection_resolution_already_reverted")
        # Delete the overlay only. ON DELETE CASCADE removes its member map;
        # immutable decisions, decision events and original graph survive.
        db.execute("DELETE FROM resolution_active_groups WHERE project=? AND resolution_id=?",
                   (project, resolution_id))
        new_revision = expected_revision + 1
        now = time.time()
        db.execute("INSERT INTO resolution_events(resolution_id,project,action,request_id,revision,at) VALUES(?,?,?,?,?,?)",
                   (resolution_id, project, "revert", request_id, new_revision, now))
        db.execute("UPDATE resolution_revisions SET revision=? WHERE project=?",
                   (new_revision, project))
        result = {"ok": True, "project": project, "resolution_id": resolution_id,
                  "state": "reverted", "revision": new_revision}
        db.execute("INSERT INTO resolution_requests VALUES(?,?,?,?,?,?)",
                   (request_id, project, "revert", payload_hash, resolution_id, encoded(result).decode()))
        return result


def _history(store, project, options):
    limit = options.get("limit", 50)
    before = options.get("before_revision")
    if type(limit) is not int or not 1 <= limit <= MAX_HISTORY:
        raise ValueError("collection_resolution_history_limit_invalid")
    if before is not None and (type(before) is not int or before <= 0):
        raise ValueError("collection_resolution_history_cursor_invalid")
    request_id = options.get("request_id")
    if request_id is not None:
        _request_id(request_id)
    if store is None:
        return {"ok": True, "project": project, "revision": 0,
                "items": [], "request_result": None, "state": "not_configured"}
    with store.database() as db:
        _require_project(db, project)
        revision = _revision(db, project)
        if not _has_feature(db):
            return {"ok": True, "project": project, "revision": 0, "items": [],
                    "request_result": None}
        rows = db.execute("""SELECT d.*,a.resolution_id AS active_id,
             (SELECT e.revision FROM resolution_events e WHERE e.resolution_id=d.resolution_id
                AND e.action='revert' ORDER BY e.sequence DESC LIMIT 1) AS reverted_revision
             FROM resolution_decisions d LEFT JOIN resolution_active_groups a
               ON a.resolution_id=d.resolution_id AND a.project=d.project
             WHERE d.project=? AND (? IS NULL OR d.applied_revision<?)
             ORDER BY d.applied_revision DESC,d.resolution_id LIMIT ?""",
             (project, before, before, limit + 1)).fetchall()
        items = []
        for row in rows[:limit]:
            members = json.loads(row["members_json"])
            permitted = _member_permissions(db, project, members)
            validity = _validity(db, store, row) if permitted else {
                "valid": False, "state": "collection_resolution_member_permission_revoked"}
            item = {"resolution_id": row["resolution_id"], "project": project,
                    "state": "active" if row["active_id"] else "reverted",
                    "active": bool(row["active_id"]), "applied_revision": row["applied_revision"],
                    "reverted_revision": row["reverted_revision"],
                    "current_validity": validity, "member_count": len(members)}
            if permitted:
                item.update({"members": members, "canonical": json.loads(row["canonical_json"]),
                             "reason": row["reason"], "source_fingerprint": row["source_fingerprint"],
                             "plan_sha256": row["plan_sha256"]})
            else:
                item.update({"members": [], "canonical": None, "reason": None, "redacted": True})
            items.append(item)
        request_result = None
        if request_id is not None:
            saved = db.execute("SELECT resolution_id,result_json FROM resolution_requests WHERE request_id=? AND project=?",
                               (request_id, project)).fetchone()
            if saved:
                decision = db.execute("SELECT members_json FROM resolution_decisions WHERE resolution_id=?",
                                      (saved["resolution_id"],)).fetchone()
                if decision and _member_permissions(db, project, json.loads(decision[0])):
                    request_result = json.loads(saved["result_json"])
        return {"ok": True, "project": project, "revision": revision, "items": items,
                "request_result": request_result,
                "next_before_revision": rows[limit - 1]["applied_revision"] if len(rows) > limit else None}


def active_resolutions(store, db, projects):
    """Read-only, project-bound, *fully current* overlays for a graph projection.

    Return [] for combined-project views; projecting a resolution into a union
    graph can incorrectly consolidate a shared ID in another project. Consumers
    must retain original edge IDs, direction and provenance for every member.
    """
    if not isinstance(projects, list) or len(projects) != 1 or not isinstance(projects[0], str):
        return []
    project = projects[0]
    if not _project_allowed(db, project) or not _has_feature(db):
        return []
    rows = db.execute("""SELECT d.* FROM resolution_active_groups a JOIN resolution_decisions d
                         ON d.resolution_id=a.resolution_id AND d.project=a.project
                         WHERE a.project=? ORDER BY d.applied_revision LIMIT ?""",
                         (project, MAX_ACTIVE_GROUPS + 1)).fetchall()
    if len(rows) > MAX_ACTIVE_GROUPS:
        raise RuntimeError("collection_resolution_active_group_budget_exceeded")
    return [{"project": project, "resolution_id": row["resolution_id"],
             "members": json.loads(row["members_json"]),
             "canonical": json.loads(row["canonical_json"]),
             "reason": row["reason"], "revision": row["applied_revision"],
             "source_fingerprint": row["source_fingerprint"],
             "current_validity": {"valid": True, "state": "current"}}
            for row in rows if _validity(db, store, row)["valid"]]


def resolution_action(state_root, action, query=None, *, explicit_opt_in=False):
    """JSON-object (or JSON-string) API for preview, apply, revert and history.

    Only apply/revert mutate the optional derived overlay; both require explicit
    opt-in. Successful request_id replays return the originally stored result.
    """
    if action not in {"collection-resolution-preview", "collection-resolution-apply",
                      "collection-resolution-revert", "collection-resolution-history"}:
        raise ValueError("collection_resolution_action_invalid")
    options = _options(query)
    if action in {"collection-resolution-apply", "collection-resolution-revert"} and explicit_opt_in is not True:
        raise ValueError("collection_resolution_explicit_opt_in_required")
    store = CollectionStore.open_existing(Path(state_root))
    if store is not None and store.schema != 3:
        raise RuntimeError("collection_resolution_requires_collection_v3")
    if action == "collection-resolution-history":
        return _history(store, _project(options), options)
    if store is None:
        raise ValueError("collection_resolution_store_not_configured")
    if action == "collection-resolution-preview":
        project = _project(options)
        members = _members(options.get("members"))
        canonical = _canonical(options.get("canonical"), members)
        reason = _reason(options.get("reason"))
        with store.database() as db:
            _require_project(db, project)
            evidence = _evidence(db, store, project, members, read_blobs=True, show=True)
            if _has_feature(db):
                ids = [member["document_id"] for member in members]
                marks = ",".join("?" for _ in ids)
                if db.execute("SELECT 1 FROM resolution_active_members WHERE project=? AND document_id IN (" +
                              marks + ") LIMIT 1", (project, *ids)).fetchone():
                    raise ValueError("collection_resolution_overlapping_active_group")
            body = {"schema": PLAN_SCHEMA, "project": project, "members": members,
                    "canonical": canonical, "reason": reason,
                    "revision": _revision(db, project),
                    "source_fingerprint": evidence["source_fingerprint"]}
            plan = {**body, "plan_sha256": digest(encoded(body))}
            if len(encoded(plan)) > MAX_PLAN_BYTES:
                raise ValueError("collection_resolution_plan_too_large")
            return {"ok": True, "plan": plan, "records": evidence["records"],
                    "relations": evidence["relations"]}
    if action == "collection-resolution-apply":
        plan = _checked_plan(options.get("plan"))
        request_id = _request_id(options.get("request_id"))
        return _apply(store, plan, request_id)
    project = _project(options)
    resolution_id = _text(options.get("resolution_id"), "resolution_id", 128)
    if not re.fullmatch(r"resolution:[0-9a-f]{64}", resolution_id):
        raise ValueError("collection_resolution_id_invalid")
    revision = options.get("expected_revision")
    if type(revision) is not int or revision < 0:
        raise ValueError("collection_resolution_revision_invalid")
    request_id = _request_id(options.get("request_id"))
    return _revert(store, project, resolution_id, revision, request_id)
