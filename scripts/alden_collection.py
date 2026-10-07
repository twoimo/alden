"""Versioned multi-target collection journal and derived logical graph.

Original stores remain authoritative. This private store retains source bytes,
identity mappings and stage receipts; it never sends messages or infers edges.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import unicodedata
import uuid


SCHEMA = 2
MAX_RECORD_BYTES = 2 * 1024 * 1024
MAX_BATCH_RECORDS = 2000
STAGES = {"discovered", "parsed", "validated", "stored", "indexed", "failed", "paused"}


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def identity(namespace: str, original_id: str) -> str:
    # Case, URL query and platform IDs may be significant. Never case-fold IDs.
    if not isinstance(original_id, str) or not original_id or len(original_id) > 4096:
        raise ValueError("collection_identity_invalid")
    return namespace + ":" + digest(json.dumps([namespace, original_id], ensure_ascii=False).encode())


def encoded(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def normalized_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value).split())


def safe_directory(path: Path) -> Path:
    path = path.absolute()
    if ".." in path.parts:
        raise ValueError("collection_path_invalid")
    for item in [*reversed(path.parents), path]:
        if item.is_symlink():
            raise ValueError("collection_path_symlink")
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    return path


class TargetBusy(RuntimeError):
    pass


class CollectionStore:
    def __init__(self, state_root: Path):
        self.read_only = False
        self.root = safe_directory(state_root / "knowledge" / "collection")
        self.blobs = safe_directory(self.root / "sources")
        self.locks = safe_directory(self.root / "locks")
        self.path = self.root / "collection.sqlite3"
        if self.path.is_symlink():
            raise ValueError("collection_path_symlink")
        if not self.path.exists():
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
        with self.database() as db:
            has_meta = db.execute("SELECT 1 FROM sqlite_master WHERE name='meta'").fetchone()
            prior = db.execute("SELECT value FROM meta WHERE key='schema'").fetchone() if has_meta else None
            if prior and int(prior[0]) not in {1, SCHEMA}:
                raise RuntimeError("collection_schema_requires_migration")
            if prior and int(prior[0]) == 1:
                # Only this derived store is migrated. Retain a consistent,
                # private recovery copy before changing its schema.
                backup = self.root / ("collection.schema-1-" + uuid.uuid4().hex + ".sqlite3")
                fd = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(fd)
                with sqlite3.connect(backup) as recovery:
                    db.backup(recovery)
                    if recovery.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise RuntimeError("collection_backup_integrity")
        with self.database() as db:
            db.execute("PRAGMA journal_mode=WAL")  # This derived store only.
            db.executescript("""
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);
                CREATE TABLE IF NOT EXISTS targets(
                  id TEXT PRIMARY KEY,platform TEXT NOT NULL,original_id TEXT NOT NULL,
                  kind TEXT NOT NULL,label TEXT NOT NULL,url TEXT NOT NULL,config TEXT NOT NULL,
                  interval_seconds INTEGER NOT NULL,enabled INTEGER NOT NULL,
                  cursor TEXT,last_success REAL,last_error TEXT,next_run REAL);
                CREATE TABLE IF NOT EXISTS target_projects(
                  target_id TEXT NOT NULL,project TEXT NOT NULL,permission TEXT NOT NULL,
                  PRIMARY KEY(target_id,project),FOREIGN KEY(target_id) REFERENCES targets(id));
                CREATE TABLE IF NOT EXISTS documents(
                  id TEXT PRIMARY KEY,platform TEXT NOT NULL,original_id TEXT NOT NULL,
                  current_version TEXT NOT NULL,availability TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS versions(
                  id TEXT PRIMARY KEY,document_id TEXT NOT NULL,raw_sha256 TEXT NOT NULL,
                  raw_path TEXT NOT NULL,label TEXT NOT NULL,body TEXT NOT NULL,metadata TEXT NOT NULL,
                  collected_at REAL NOT NULL,UNIQUE(document_id,raw_sha256),
                  FOREIGN KEY(document_id) REFERENCES documents(id));
                CREATE TABLE IF NOT EXISTS memberships(
                  target_id TEXT NOT NULL,document_id TEXT NOT NULL,current_version TEXT,
                  PRIMARY KEY(target_id,document_id),FOREIGN KEY(target_id) REFERENCES targets(id),
                  FOREIGN KEY(document_id) REFERENCES documents(id));
                CREATE TABLE IF NOT EXISTS relations(
                  id TEXT PRIMARY KEY,target_id TEXT NOT NULL,source TEXT NOT NULL,
                  target TEXT NOT NULL,type TEXT NOT NULL,evidence TEXT NOT NULL,version TEXT NOT NULL,
                  active INTEGER NOT NULL DEFAULT 1,FOREIGN KEY(target_id) REFERENCES targets(id));
                CREATE TABLE IF NOT EXISTS runs(
                  id TEXT PRIMARY KEY,target_id TEXT NOT NULL,origin TEXT NOT NULL,state TEXT NOT NULL,
                  started_at REAL NOT NULL,ended_at REAL,cursor_before TEXT,cursor_after TEXT,
                  added INTEGER NOT NULL DEFAULT 0,revised INTEGER NOT NULL DEFAULT 0,
                  unchanged INTEGER NOT NULL DEFAULT 0,error TEXT);
                CREATE TABLE IF NOT EXISTS events(
                  sequence INTEGER PRIMARY KEY AUTOINCREMENT,event_id TEXT NOT NULL UNIQUE,
                  run_id TEXT NOT NULL,target_id TEXT NOT NULL,stage TEXT NOT NULL,
                  at REAL NOT NULL,document_id TEXT,version TEXT,details TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS event_run ON events(run_id,sequence);
                CREATE INDEX IF NOT EXISTS event_document ON events(document_id,target_id,stage,sequence DESC);
                CREATE INDEX IF NOT EXISTS membership_doc ON memberships(document_id,target_id);
                CREATE VIRTUAL TABLE IF NOT EXISTS document_search USING fts5(
                  document_id UNINDEXED,label,body,tokenize='unicode61');
                CREATE VIRTUAL TABLE IF NOT EXISTS version_search USING fts5(
                  version_id UNINDEXED,document_id UNINDEXED,label,body,tokenize='unicode61');
            """)
            # Keep ALTER, lineage backfill, index data and schema marker in
            # one transaction so an interrupted migration can be retried.
            db.execute("BEGIN IMMEDIATE")
            version = db.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
            if version and int(version[0]) == 1:
                db.execute("ALTER TABLE memberships ADD COLUMN current_version TEXT")
                # An authorized membership sees the version its target stored,
                # never a different target's global current version.
                db.execute("""UPDATE memberships SET current_version=(
                  SELECT e.version FROM events e WHERE e.document_id=memberships.document_id
                  AND e.target_id=memberships.target_id AND e.stage='stored'
                  ORDER BY e.sequence DESC LIMIT 1)""")
                db.execute("INSERT INTO version_search SELECT id,document_id,label,body FROM versions")
            db.execute("INSERT INTO meta VALUES('schema',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(SCHEMA),))

    @classmethod
    def open_existing(cls, state_root: Path):
        path = state_root.absolute() / "knowledge" / "collection" / "collection.sqlite3"
        if not path.exists():
            return None
        if any(p.is_symlink() for p in [*path.parents, path]):
            raise ValueError("collection_path_symlink")
        store = cls.__new__(cls)
        store.read_only = True
        store.path, store.root = path, path.parent
        store.blobs, store.locks = store.root / "sources", store.root / "locks"
        with store.database() as db:
            row = db.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
            if row is None or int(row[0]) != SCHEMA:
                raise RuntimeError("collection_schema_requires_migration")
        return store

    @contextmanager
    def database(self):
        db = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=.15) if self.read_only else sqlite3.connect(self.path, timeout=.15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        if self.read_only:
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @contextmanager
    def target_lock(self, target_id: str):
        path = self.locks / (digest(target_id.encode()) + ".lock")
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise TargetBusy(target_id) from error
            yield
        finally:
            os.close(fd)

    def register(self, *, platform: str, original_id: str, kind: str, label: str,
                 projects: list[str], url: str = "", interval_seconds: int = 21600,
                 enabled: bool = True, config: dict | None = None,
                 permission: str = "local-private") -> str:
        if platform not in {"threads", "youtube", "files", "graph"}:
            raise ValueError("collection_platform_invalid")
        if not projects or any(not isinstance(p, str) or not p or len(p) > 128 for p in projects):
            raise ValueError("collection_projects_invalid")
        if type(interval_seconds) is not int or not 60 <= interval_seconds <= 30 * 86400:
            raise ValueError("collection_interval_invalid")
        target_id = identity("target-" + platform, kind + ":" + original_id)
        with self.target_lock(target_id), self.database() as db:
            db.execute("""INSERT INTO targets(id,platform,original_id,kind,label,url,config,interval_seconds,enabled,next_run)
              VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET label=excluded.label,url=excluded.url,
              config=excluded.config,interval_seconds=excluded.interval_seconds,enabled=excluded.enabled""",
              (target_id, platform, original_id, kind, label, url, encoded(config or {}).decode(), interval_seconds, int(enabled), time.time()))
            db.execute("DELETE FROM target_projects WHERE target_id=?", (target_id,))
            db.executemany("INSERT INTO target_projects VALUES(?,?,?)",
                           [(target_id, p, permission) for p in sorted(set(projects))])
        return target_id

    def target(self, target_id: str) -> dict:
        with self.database() as db:
            row = db.execute("SELECT * FROM targets WHERE id=?", (target_id,)).fetchone()
            if row is None:
                raise ValueError("collection_target_missing")
            data = dict(row)
            data["config"] = json.loads(data["config"])
            data["projects"] = [dict(x) for x in db.execute("SELECT project,permission FROM target_projects WHERE target_id=?", (target_id,))]
            return data

    def _blob(self, raw: bytes) -> tuple[str, str]:
        if len(raw) > MAX_RECORD_BYTES:
            raise ValueError("collection_record_too_large")
        sha = digest(raw)
        path = self.blobs / (sha + ".json")
        if path.exists():
            if path.is_symlink() or digest(path.read_bytes()) != sha:
                raise RuntimeError("collection_source_integrity")
        else:
            fd, temporary = tempfile.mkstemp(prefix=".source-", dir=self.blobs)
            try:
                with os.fdopen(fd, "wb") as out:
                    out.write(raw)
                    out.flush()
                    os.fsync(out.fileno())
                try:
                    os.link(temporary, path)
                except FileExistsError:
                    if path.is_symlink() or digest(path.read_bytes()) != sha:
                        raise RuntimeError("collection_source_integrity")
            finally:
                os.unlink(temporary)
        return sha, path.name

    @staticmethod
    def _event(db, run_id, target_id, stage, *, document_id=None, version=None, **details):
        if stage not in STAGES:
            raise ValueError("collection_stage_invalid")
        event_id = identity("event", encoded([run_id, stage, document_id, version]).decode())
        db.execute("INSERT OR IGNORE INTO events(event_id,run_id,target_id,stage,at,document_id,version,details) VALUES(?,?,?,?,?,?,?,?)",
                   (event_id, run_id, target_id, stage, time.time(), document_id, version, encoded(details).decode()))

    def ingest(self, target_id: str, records, *, cursor=None, origin="alden",
               run_id: str | None = None, relations=(), cancelled=lambda: False) -> dict:
        run_id = run_id or uuid.uuid4().hex
        with self.target_lock(target_id):
            target = self.target(target_id)
            if not target["enabled"]:
                raise RuntimeError("collection_target_paused")
            counts = {"added": 0, "revised": 0, "unchanged": 0}
            with self.database() as db:
                previous = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
                if previous:
                    if previous["target_id"] != target_id or previous["origin"] != origin:
                        raise RuntimeError("collection_run_identity_conflict")
                    if previous["state"] == "complete":
                        return dict(previous)
                db.execute("INSERT OR REPLACE INTO runs(id,target_id,origin,state,started_at,cursor_before) VALUES(?,?,?,?,?,?)",
                           (run_id, target_id, origin, "running", time.time(), target["cursor"]))
            try:
                with self.database() as db:
                    for index, record in enumerate(records):
                        if index >= MAX_BATCH_RECORDS:
                            raise ValueError("collection_batch_budget")
                        if cancelled():
                            raise RuntimeError("collection_cancelled")
                        platform = record.get("platform", target["platform"])
                        original_id = record["original_id"]
                        doc_id = identity(platform, original_id)
                        raw = encoded(record.get("raw", record))
                        raw_sha, raw_path = self._blob(raw)
                        version = identity("version", doc_id + ":" + raw_sha)
                        label = normalized_text(str(record.get("label", original_id)))[:1024]
                        body = normalized_text(str(record.get("text", "")))
                        metadata = {k: v for k, v in record.items() if k not in {"raw", "text"}}
                        old = db.execute("SELECT current_version FROM documents WHERE id=?", (doc_id,)).fetchone()
                        membership = db.execute("SELECT current_version FROM memberships WHERE target_id=? AND document_id=?", (target_id, doc_id)).fetchone()
                        prior_version = membership[0] if membership else old[0] if old else None
                        category = "added" if old is None else "unchanged" if prior_version == version else "revised"
                        counts[category] += 1
                        self._event(db, run_id, target_id, "discovered", document_id=doc_id, version=version)
                        db.execute("INSERT INTO documents VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET current_version=excluded.current_version,availability=excluded.availability",
                                   (doc_id, platform, original_id, version, "available"))
                        inserted = db.execute("INSERT OR IGNORE INTO versions VALUES(?,?,?,?,?,?,?,?)",
                                   (version, doc_id, raw_sha, raw_path, label, body, encoded(metadata).decode(), time.time()))
                        if inserted.rowcount:
                            db.execute("INSERT INTO version_search VALUES(?,?,?,?)", (version, doc_id, label, body))
                        self._event(db, run_id, target_id, "parsed", document_id=doc_id, version=version)
                        self._event(db, run_id, target_id, "validated", document_id=doc_id, version=version,
                                    validation="identity, byte hash and shape only; not semantic truth")
                        db.execute("""INSERT INTO memberships VALUES(?,?,?)
                          ON CONFLICT(target_id,document_id) DO UPDATE SET current_version=excluded.current_version""",
                                   (target_id, doc_id, version))
                        self._event(db, run_id, target_id, "stored", document_id=doc_id, version=version, change=category)
                        db.execute("DELETE FROM document_search WHERE document_id=?", (doc_id,))
                        db.execute("INSERT INTO document_search VALUES(?,?,?)", (doc_id, label, body))
                        self._event(db, run_id, target_id, "indexed", document_id=doc_id, version=version,
                                    index="sqlite_fts5", dense_index="not_yet_confirmed")
                    for relation in relations:
                        source = identity(relation["source_platform"], relation["source_id"])
                        destination = identity(relation["target_platform"], relation["target_id"])
                        if not all(db.execute("SELECT 1 FROM documents WHERE id=?", (value,)).fetchone() for value in (source, destination)):
                            raise ValueError("collection_orphan_relation")
                        rel_id = identity("relation", encoded([target_id, source, relation["type"], destination, relation.get("original_id", "")]).decode())
                        evidence = encoded(relation.get("evidence", {})).decode()
                        db.execute("INSERT INTO relations VALUES(?,?,?,?,?,?,?,1) ON CONFLICT(id) DO UPDATE SET evidence=excluded.evidence,version=excluded.version,active=1",
                                   (rel_id, target_id, source, destination, relation["type"], evidence, digest(evidence.encode())))
                    if cancelled():
                        raise RuntimeError("collection_cancelled")
                    now = time.time()
                    db.execute("UPDATE runs SET state='complete',ended_at=?,cursor_after=?,added=?,revised=?,unchanged=? WHERE id=?",
                               (now, encoded(cursor).decode(), counts["added"], counts["revised"], counts["unchanged"], run_id))
                    db.execute("UPDATE targets SET cursor=?,last_success=?,last_error=NULL,next_run=? WHERE id=?",
                               (encoded(cursor).decode(), now, now + target["interval_seconds"], target_id))
                return {"run_id": run_id, "target_id": target_id, "state": "complete", **counts}
            except Exception as error:
                reason = str(error)[:200]
                state = "paused" if reason == "collection_cancelled" else "failed"
                with self.database() as db:
                    db.execute("UPDATE runs SET state=?,ended_at=?,error=? WHERE id=?", (state, time.time(), reason, run_id))
                    db.execute("UPDATE targets SET last_error=? WHERE id=?", (reason, target_id))
                    self._event(db, run_id, target_id, state, reason=reason)
                raise

    def events(self, *, after=0, limit=100, projects=None, target_id=None, stage=None) -> dict:
        if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("collection_event_cursor_invalid")
        if projects is None:
            projects = [p["project"] for p in self.projects()]
        clauses = ["e.sequence>?"];args = [after]
        if projects is not None:
            if not projects:
                return {"items": [], "cursor": after}
            clauses.append("EXISTS(SELECT 1 FROM target_projects p WHERE p.target_id=e.target_id AND p.permission!='denied' AND p.project IN (" + ",".join("?" for _ in projects) + "))")
            args += list(projects)
        if target_id:
            clauses.append("e.target_id=?");args.append(target_id)
        if stage:
            clauses.append("e.stage=?");args.append(stage)
        with self.database() as db:
            rows = db.execute("SELECT e.*,t.label AS target_label,t.platform FROM events e JOIN targets t ON t.id=e.target_id WHERE " + " AND ".join(clauses) + " ORDER BY e.sequence LIMIT ?", (*args, limit)).fetchall()
            items = [{**dict(row), "details": json.loads(row["details"])} for row in rows]
            return {"items": items, "cursor": items[-1]["sequence"] if items else after}

    def graph(self, *, projects: list[str]) -> dict:
        if not projects:
            return {"nodes": [], "edges": [], "scope": []}
        marks = ",".join("?" for _ in projects)
        with self.database() as db:
            rows = db.execute("""SELECT d.*,s.current_version AS visible_version,v.label,v.metadata,v.collected_at
              FROM documents d JOIN (""" + self._scope_versions(projects) + """) s ON s.document_id=d.id
              JOIN versions v ON v.id=s.current_version""", projects).fetchall()
            nodes = []
            for row in rows:
                metadata = json.loads(row["metadata"])
                nodes.append({"id": row["id"], "label": row["label"], "category": metadata.get("kind", "document"),
                              "importance": 20, "updated_at": row["collected_at"], "description": metadata.get("url", ""),
                              "evidence": {"kind": "snapshot", "source_event_ids": [row["original_id"]],
                                           "chat_id": "", "confirmed_at": None, "retracted": row["availability"] == "deleted"},
                              "availability": row["availability"], "source_version": row["visible_version"]})
            allowed = {n["id"] for n in nodes}
            edges = []
            for row in db.execute("SELECT r.* FROM relations r WHERE active=1 AND EXISTS(SELECT 1 FROM target_projects p WHERE p.target_id=r.target_id AND p.project IN (" + marks + ") AND p.permission!='denied')", projects):
                if row["source"] in allowed and row["target"] in allowed:
                    edges.append({"id": row["id"], "source": row["source"], "target": row["target"],
                                  "relation": row["type"], "weight": 1, "context": "explicit source relationship",
                                  "purpose": "reference", "evidence_message_id": row["id"],
                                  "evidence": {"kind": "snapshot", "source_event_ids": [row["id"]], "chat_id": "", "confirmed_at": None, "retracted": False},
                                  "source_evidence": json.loads(row["evidence"])})
            return {"nodes": nodes, "edges": edges, "scope": projects, "canonical": "independent original sources; this is a derived projection"}

    def search(self, query: str, *, projects: list[str], limit=10) -> list[dict]:
        if not projects:
            return []
        with self.database() as db:
            rows = db.execute("""SELECT d.id,v.label,v.body,v.metadata,bm25(version_search) AS rank
              FROM version_search JOIN documents d ON d.id=version_search.document_id
              JOIN (""" + self._scope_versions(projects) + """) s ON s.document_id=d.id
              AND s.current_version=version_search.version_id JOIN versions v ON v.id=s.current_version
              WHERE version_search MATCH ? AND d.availability='available' ORDER BY rank,d.id LIMIT ?""",
              (*projects, query, limit)).fetchall()
            return [{**dict(row), "metadata": json.loads(row["metadata"])} for row in rows]

    @staticmethod
    def _scope_versions(projects: list[str]) -> str:
        marks = ",".join("?" for _ in projects)
        return """SELECT document_id,current_version FROM (
          SELECT m.document_id,m.current_version,ROW_NUMBER() OVER(
            PARTITION BY m.document_id ORDER BY v.collected_at DESC,m.target_id) AS choice
          FROM memberships m JOIN versions v ON v.id=m.current_version
          WHERE EXISTS(SELECT 1 FROM target_projects p WHERE p.target_id=m.target_id
            AND p.permission!='denied' AND p.project IN (""" + marks + "))) WHERE choice=1"

    def projects(self) -> list[dict]:
        with self.database() as db:
            return [dict(row) for row in db.execute("SELECT project,COUNT(DISTINCT target_id) AS targets FROM target_projects WHERE permission!='denied' GROUP BY project ORDER BY project")]

    def targets(self, projects: list[str]) -> list[dict]:
        if not projects:
            return []
        with self.database() as db:
            return [dict(row) for row in db.execute("""SELECT t.id,t.label,t.platform FROM targets t
              WHERE EXISTS(SELECT 1 FROM target_projects p WHERE p.target_id=t.id
              AND p.permission!='denied' AND p.project IN (""" + ",".join("?" for _ in projects) + ")) ORDER BY t.label,t.id", projects)]

    def graph_page(self, *, projects: list[str], limit=120, offset=0, focus=None, hops=1, query="") -> dict:
        if type(limit) is not int or not 1 <= limit <= 120 or type(offset) is not int or offset < 0 or type(hops) is not int or not 0 <= hops <= 3:
            raise ValueError("collection_graph_budget_invalid")
        if not projects:
            return {"ok": True, "nodes": [], "edges": [], "total_nodes": 0, "total_edges": 0, "next": None}
        with self.database() as db:
            marks = ",".join("?" for _ in projects)
            versions = self._scope_versions(projects)
            scope = "SELECT document_id FROM (" + versions + ")"
            total = db.execute("SELECT COUNT(*) FROM (" + scope + ")", projects).fetchone()[0]
            edge_scope = "SELECT r.* FROM relations r WHERE r.active=1 AND r.source IN (" + scope + ") AND r.target IN (" + scope + ") AND EXISTS(SELECT 1 FROM target_projects p WHERE p.target_id=r.target_id AND p.permission!='denied' AND p.project IN (" + marks + "))"
            edge_args = [*projects, *projects, *projects]
            total_edges = db.execute("SELECT COUNT(*) FROM (" + edge_scope + ")", edge_args).fetchone()[0]
            if focus:
                if not db.execute("SELECT 1 FROM (" + scope + ") WHERE document_id=?", (*projects, focus)).fetchone():
                    raise ValueError("collection_graph_focus_not_in_scope")
                ids = [focus]
                for _ in range(hops):
                    current = ",".join("?" for _ in ids)
                    neighbors = db.execute("SELECT source,target FROM (" + edge_scope + ") WHERE source IN (" + current + ") OR target IN (" + current + ") LIMIT 144", (*edge_args, *ids, *ids)).fetchall()
                    for edge in neighbors:
                        for identifier in edge:
                            if identifier not in ids and len(ids) < min(limit, 24):
                                ids.append(identifier)
                more = False
            elif query:
                hits = self.search(query, projects=projects, limit=offset+limit+1)
                ids = [item["id"] for item in hits[offset:offset+limit]]
                more = len(hits) > offset+limit
            else:
                rows = db.execute("SELECT document_id FROM (" + scope + ") ORDER BY document_id LIMIT ? OFFSET ?", (*projects, limit+1, offset)).fetchall()
                ids = [row[0] for row in rows[:limit]]
                more = len(rows) > limit
            if not ids:
                return {"ok": True, "nodes": [], "edges": [], "total_nodes": total, "total_edges": total_edges, "next": None}
            selected = ",".join("?" for _ in ids)
            rows = db.execute("SELECT d.*,s.current_version AS visible_version,v.label,v.body,v.metadata,v.collected_at FROM documents d JOIN (" + versions + ") s ON s.document_id=d.id JOIN versions v ON v.id=s.current_version WHERE d.id IN (" + selected + ") ORDER BY d.id", (*projects, *ids)).fetchall()
            nodes = []
            for row in rows:
                meta = json.loads(row["metadata"])
                nodes.append({"id": row["id"], "label": row["label"], "category": meta.get("kind", "document"),
                              "importance": 20, "updated_at": row["collected_at"], "description": row["body"][:2400],
                              "space": ", ".join(projects), "source_version": row["visible_version"],
                              "source_metadata": meta, "evidence": {"kind": "snapshot", "source_event_ids": [row["original_id"]], "chat_id": "", "confirmed_at": None, "retracted": row["availability"] == "deleted"}})
            edges = [{"id": row["id"], "source": row["source"], "target": row["target"], "relation": row["type"], "context": "explicit source relationship", "weight": 1, "purpose": "reference", "evidence_message_id": row["id"], "evidence": {"kind": "snapshot", "source_event_ids": [row["id"]], "chat_id": "", "confirmed_at": None, "retracted": False}}
                     for row in db.execute("SELECT * FROM (" + edge_scope + ") WHERE source IN (" + selected + ") AND target IN (" + selected + ") LIMIT 144", (*edge_args, *ids, *ids))]
            return {"ok": True, "nodes": nodes, "edges": edges, "total_nodes": total, "total_edges": total_edges,
                    "next": offset+limit if more else None, "focus": focus, "scope": projects,
                    "display_scope": "bounded derived projection; not full graph"}

    def recent_events(self, *, before=None, after=None, limit=50, projects=None,
                      target_id=None, platform=None, stage=None, query="", since=None, until=None) -> dict:
        if (type(limit) is not int or not 1 <= limit <= 200
            or (before is not None and (type(before) is not int or before <= 0))
            or (after is not None and (type(after) is not int or after < 0))
            or (before is not None and after is not None)):
            raise ValueError("collection_event_cursor_invalid")
        if stage is not None and stage not in STAGES:
            raise ValueError("collection_stage_invalid")
        if platform is not None and platform not in {"youtube", "threads", "files", "graph"}:
            raise ValueError("collection_platform_invalid")
        for value in (since, until):
            if value is not None and (type(value) not in {int, float} or not math.isfinite(value) or value < 0):
                raise ValueError("collection_event_time_invalid")
        if since is not None and until is not None and since >= until:
            raise ValueError("collection_event_time_invalid")
        if projects is None:
            projects = [p["project"] for p in self.projects()]
        clauses = ["(? IS NULL OR e.sequence<?)"];args = [before,before]
        if after is not None:
            clauses.append("e.sequence>?");args.append(after)
        if projects is not None:
            if not projects:
                return {"ok": True, "items": [], "next": None}
            clauses.append("EXISTS(SELECT 1 FROM target_projects p WHERE p.target_id=e.target_id AND p.permission!='denied' AND p.project IN (" + ",".join("?" for _ in projects) + "))");args += projects
        if target_id:
            clauses.append("e.target_id=?");args.append(target_id)
        if platform:
            clauses.append("t.platform=?");args.append(platform)
        if stage:
            clauses.append("e.stage=?");args.append(stage)
        if since is not None:
            clauses.append("e.at>=?");args.append(since)
        if until is not None:
            clauses.append("e.at<?");args.append(until)
        if query:
            clauses.append("(t.label LIKE ? OR v.label LIKE ?)");args += ["%"+query+"%"]*2
        with self.database() as db:
            order = "ASC" if after is not None else "DESC"
            rows = db.execute("SELECT e.*,t.label AS target_label,t.platform,v.label AS document_label,v.metadata FROM events e JOIN targets t ON t.id=e.target_id LEFT JOIN versions v ON v.id=e.version WHERE " + " AND ".join(clauses) + " ORDER BY e.sequence " + order + " LIMIT ?", (*args,limit+1)).fetchall()
            items = []
            for row in rows[:limit]:
                item = dict(row);item["details"] = json.loads(item["details"])
                meta = json.loads(item.pop("metadata") or "{}")
                item["source_url"] = meta.get("url", "")
                item["projects"] = [x[0] for x in db.execute("SELECT project FROM target_projects WHERE target_id=? AND permission!='denied'", (item["target_id"],))]
                items.append(item)
            return {"ok": True, "items": items, "next": items[-1]["sequence"] if len(rows)>limit else None,
                    "cursor": items[-1]["sequence"] if after is not None and items else after}


def read_action(state_root: Path, action: str, query: str | None = None) -> dict:
    """Bounded read-only application boundary; never creates a store on lookup."""
    if action not in {"collection-projects", "collection-history", "collection-graph"}:
        raise ValueError("collection_action_invalid")
    options = json.loads(query) if query else {}
    if not isinstance(options, dict):
        raise ValueError("collection_query_invalid")
    store = CollectionStore.open_existing(state_root)
    if store is None:
        return {"ok": True, "items": [], "nodes": [], "edges": [], "projects": [], "next": None, "state": "not_configured"}
    known = [p["project"] for p in store.projects()]
    projects = options.get("projects", known)
    if not isinstance(projects, list) or len(projects) > 16 or any(not isinstance(p, str) or p not in known for p in projects):
        raise ValueError("collection_project_scope_invalid")
    if action == "collection-projects":
        return {"ok": True, "projects": store.projects()}
    if action == "collection-history":
        result = store.recent_events(before=options.get("before"), after=options.get("after"),
                                     limit=options.get("limit",50), projects=projects,
                                     target_id=options.get("target_id"), platform=options.get("platform"),
                                     stage=options.get("stage"), query=str(options.get("search", ""))[:256],
                                     since=options.get("since"), until=options.get("until"))
        result["projects"] = store.projects()
        result["targets"] = store.targets(projects)
        return result
    if action == "collection-graph":
        return store.graph_page(projects=projects, limit=options.get("limit",120), offset=options.get("offset",0),
                                focus=options.get("focus"), hops=options.get("hops",1), query=str(options.get("search", ""))[:256])
    raise ValueError("collection_action_invalid")
