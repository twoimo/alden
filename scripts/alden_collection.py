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
import re
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

    def _blob(self, raw: bytes, *, folder=None, budget=MAX_RECORD_BYTES) -> tuple[str, str]:
        if len(raw) > budget:
            raise ValueError("collection_record_too_large")
        folder = folder or self.blobs
        sha = digest(raw)
        path = folder / (sha + ".json")
        if path.exists():
            if path.is_symlink() or digest(path.read_bytes()) != sha:
                raise RuntimeError("collection_source_integrity")
        else:
            fd, temporary = tempfile.mkstemp(prefix=".source-", dir=folder)
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

    def retain_source_capture(self, target_id: str, raw: bytes, manifest: dict, *, expected_cursor: str, cancelled=lambda: False) -> dict:
        """Add evidence to an already committed projection without reindexing it."""
        with self.target_lock(target_id):
            if cancelled():
                raise RuntimeError("collection_cancelled")
            target = self.target(target_id)
            if not target["enabled"]:
                raise RuntimeError("collection_target_paused")
            if not any(p["permission"] != "denied" for p in target["projects"]):
                raise ValueError("collection_source_scope_denied")
            if target["cursor"] != expected_cursor:
                raise RuntimeError("collection_source_checkpoint_changed")
            with self.database() as db:
                saved = {r["document_id"]: r["current_version"] for r in db.execute(
                    "SELECT document_id,current_version FROM memberships WHERE target_id=?", (target_id,))}
                refs = manifest["records"]
                if set(saved) != {r["document_id"] for r in refs} or any(saved.get(r["document_id"]) != r["version"] for r in refs):
                    raise RuntimeError("collection_source_projection_changed")
                if "relations" in manifest:
                    relationships = {r["id"]: r["version"] for r in db.execute(
                        "SELECT id,version FROM relations WHERE target_id=? AND active=1", (target_id,))}
                    if relationships != manifest["relations"]:
                        raise RuntimeError("collection_source_relations_changed")
            folder = safe_directory(self.root / "source-captures")
            prior_capture = json.loads(expected_cursor).get("source_capture", {})
            if prior_capture.get("sha256") == digest(raw) and prior_capture.get("byte_scope") == manifest["byte_scope"]:
                prior_name = prior_capture.get("manifest", "")
                if not isinstance(prior_name, str) or not re.fullmatch(r"[0-9a-f]{64}\.json", prior_name):
                    raise RuntimeError("collection_source_manifest_invalid")
                prior_path = folder / prior_name
                if prior_path.is_symlink():
                    raise RuntimeError("collection_source_integrity")
                prior_bytes = prior_path.read_bytes()
                if digest(prior_bytes) + ".json" != prior_name:
                    raise RuntimeError("collection_source_integrity")
                prior = json.loads(prior_bytes)
                # A live SQLite backup header may change while the permitted
                # export stays identical. Reuse the original evidence receipt.
                if all(prior.get(k) == manifest.get(k) for k in ["records", "relations", "processing_version", "origin", "adapter"]):
                    if not isinstance(prior.get("source_file"), str) or not re.fullmatch(r"[0-9a-f]{64}\.json", prior["source_file"]):
                        raise RuntimeError("collection_source_manifest_invalid")
                    archived = folder / prior["source_file"]
                    if archived.is_symlink() or digest(archived.read_bytes()) != digest(raw):
                        raise RuntimeError("collection_source_integrity")
                    if cancelled():
                        raise RuntimeError("collection_cancelled")
                    return {"state": "unchanged", "manifest": prior_name, "sha256": digest(raw)}
            sha, name = self._blob(raw, folder=folder, budget=32 * 1024 * 1024)
            receipt = {**manifest, "source_sha256": sha, "source_bytes": len(raw), "source_file": name}
            manifest_sha, manifest_name = self._blob(encoded(receipt), folder=folder, budget=16 * 1024 * 1024)
            run_id = identity("source-capture", target_id + ":" + manifest_sha)
            with self.database() as db:
                current = db.execute("SELECT cursor FROM targets WHERE id=?", (target_id,)).fetchone()
                if current[0] != expected_cursor:
                    raise RuntimeError("collection_source_checkpoint_changed")
                previous = db.execute("SELECT 1 FROM runs WHERE id=? AND state='complete'", (run_id,)).fetchone()
                if previous:
                    return {"state": "unchanged", "run_id": run_id, "manifest": manifest_name, "sha256": sha}
                if cancelled():
                    raise RuntimeError("collection_cancelled")
                cursor = json.loads(expected_cursor)
                cursor["source_capture"] = {"schema": 1, "manifest": manifest_name,
                                            "sha256": sha, "byte_scope": manifest["byte_scope"]}
                now = time.time()
                db.execute("""INSERT INTO runs(id,target_id,origin,state,started_at,ended_at,cursor_before,cursor_after)
                  VALUES(?,?,?,?,?,?,?,?)""", (run_id, target_id, "alden-source-capture", "complete", now, now,
                                              expected_cursor, encoded(cursor).decode()))
                db.execute("UPDATE targets SET cursor=? WHERE id=?", (encoded(cursor).decode(), target_id))
                self._event(db, run_id, target_id, "stored", change="source_evidence",
                            source_manifest=manifest_name, source_sha256=sha, byte_scope=manifest["byte_scope"],
                            reason="원문 근거를 보존했습니다. 지식 항목·관계·검색 색인은 변경하지 않았습니다.")
            return {"state": "complete", "run_id": run_id, "manifest": manifest_name, "sha256": sha}

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

    def graph_page(self, *, projects: list[str], limit=120, offset=0, focus=None, hops=1, query="",
                   target_id=None, platform=None, node_type=None, relation=None, since=None, until=None,
                   details=False, expected_version=None) -> dict:
        if type(limit) is not int or not 1 <= limit <= 120 or type(offset) is not int or offset < 0 or type(hops) is not int or not 0 <= hops <= 3:
            raise ValueError("collection_graph_budget_invalid")
        for value in (focus, target_id, node_type, relation, expected_version):
            if value is not None and (not isinstance(value, str) or not value or len(value) > 256):
                raise ValueError("collection_graph_filter_invalid")
        if platform is not None and platform not in {"youtube", "threads", "files", "graph"}:
            raise ValueError("collection_platform_invalid")
        for value in (since, until):
            if value is not None and (type(value) not in {int, float} or not math.isfinite(value) or value < 0):
                raise ValueError("collection_event_time_invalid")
        if since is not None and until is not None and since >= until:
            raise ValueError("collection_event_time_invalid")
        if type(details) is not bool or not isinstance(query, str) or len(query) > 256:
            raise ValueError("collection_graph_filter_invalid")
        with self.database() as db:
            checkpoint, _, _, _ = self._activity_scope(db, projects, target_id, platform)
            if not projects:
                return {"ok": True, "nodes": [], "edges": [], "total_nodes": 0, "total_edges": 0,
                        "next": None, "activity_checkpoint": checkpoint}
            marks = ",".join("?" for _ in projects)
            # Materialize the permitted versions once per SQL statement. Target
            # selection precedes version ranking, so another target's revision
            # cannot replace the selected target's record.
            cte = """WITH ranked AS MATERIALIZED (
              SELECT m.document_id,m.current_version,m.target_id,ROW_NUMBER() OVER(
                PARTITION BY m.document_id ORDER BY v.collected_at DESC,m.target_id) AS choice
              FROM memberships m JOIN versions v ON v.id=m.current_version JOIN targets t ON t.id=m.target_id
              WHERE EXISTS(SELECT 1 FROM target_projects p WHERE p.target_id=m.target_id
                AND p.permission!='denied' AND p.project IN (""" + marks + """))
                AND (? IS NULL OR m.target_id=?) AND (? IS NULL OR t.platform=?)),
              visible AS MATERIALIZED (
              SELECT d.id,d.platform,d.original_id,s.current_version AS visible_version,s.target_id,v.collected_at
              FROM ranked s JOIN documents d ON d.id=s.document_id JOIN versions v ON v.id=s.current_version
              WHERE s.choice=1 AND d.availability='available' AND (? IS NULL OR json_extract(v.metadata,'$.kind')=?)
                AND (? IS NULL OR v.collected_at>=?) AND (? IS NULL OR v.collected_at<?)),
              permitted_relationships AS MATERIALIZED (
              SELECT r.* FROM relations r JOIN visible a ON a.id=r.source JOIN visible b ON b.id=r.target
              WHERE r.active=1 AND (? IS NULL OR r.target_id=?)
                AND EXISTS(SELECT 1 FROM target_projects p WHERE p.target_id=r.target_id
                  AND p.permission!='denied' AND p.project IN (""" + marks + """))),
              permitted_edges AS MATERIALIZED (SELECT * FROM permitted_relationships WHERE ? IS NULL OR type=?),
              shown AS MATERIALIZED (SELECT * FROM visible WHERE ? IS NULL OR id IN (
                SELECT source FROM permitted_edges UNION SELECT target FROM permitted_edges)),
              degrees AS (SELECT id,COUNT(DISTINCT neighbor) AS degree FROM (
                SELECT source AS id,target AS neighbor FROM permitted_edges WHERE source!=target
                UNION ALL SELECT target AS id,source AS neighbor FROM permitted_edges WHERE source!=target) GROUP BY id) """
            args = [*projects, target_id, target_id, platform, platform, node_type, node_type,
                    since, since, until, until, target_id, target_id, *projects, relation, relation, relation]
            fields = """s.id,s.platform,s.original_id,s.visible_version,s.target_id,s.collected_at,
              v.label,v.body,v.metadata,v.raw_sha256,v.raw_path,json_extract(v.metadata,'$.source_url') AS source_url"""
            if details and focus and hops == 0 and relation is None:
                # An already-selected record needs one exact permitted lookup,
                # not repeated materialization of all 26k nodes and 48k edges.
                exact = cte.replace("WHERE EXISTS(", "WHERE m.document_id=? AND EXISTS(", 1)
                row = db.execute(exact + "SELECT " + fields + " FROM shown s JOIN versions v ON v.id=s.visible_version WHERE s.id=?", (focus, *args, focus)).fetchone()
                if row is None:
                    raise ValueError("collection_graph_focus_not_in_scope")
                return self._record_details(db, row, expected_version)
            json_node = """json_object('id',s.id,'platform',s.platform,'original_id',s.original_id,
              'visible_version',s.visible_version,'target_id',s.target_id,'collected_at',s.collected_at,
              'label',v.label,'body',substr(v.body,1,2400),'metadata',json_object('kind',substr(CAST(json_extract(v.metadata,'$.kind') AS TEXT),1,256)),
              'source_url',json_extract(v.metadata,'$.source_url'),'degree',COALESCE(d.degree,0))"""
            if not focus:
                # All overview outputs share one materialized permitted scope.
                # LIMIT+1 remains inside SQL, including FTS pages.
                fts = ' AND '.join('"' + term.replace('"', '""') + '"' for term in query.split())
                picking = ("SELECT s.id FROM version_search JOIN shown s ON s.visible_version=version_search.version_id WHERE version_search MATCH ? ORDER BY bm25(version_search),s.id" if fts else
                           "SELECT s.id FROM shown s LEFT JOIN degrees d ON d.id=s.id ORDER BY COALESCE(d.degree,0) DESC,s.id")
                pick_args = [fts, limit+1, offset, limit] if fts else [limit+1, offset, limit]
                overview = cte + ",picked AS MATERIALIZED (" + picking + " LIMIT ? OFFSET ?),selected AS MATERIALIZED (SELECT id FROM picked LIMIT ?) "
                nodes_json, edges_json, total, total_edges, more, kinds, types = db.execute(overview + """SELECT
                  (SELECT json_group_array(json(item)) FROM (SELECT """ + json_node + """ AS item
                    FROM selected p JOIN shown s ON s.id=p.id JOIN versions v ON v.id=s.visible_version
                    LEFT JOIN degrees d ON d.id=s.id ORDER BY s.id)),
                  (SELECT json_group_array(json(item)) FROM (SELECT json_object('id',r.id,'source',r.source,'target',r.target,'type',r.type) AS item
                    FROM permitted_edges r JOIN selected a ON a.id=r.source JOIN selected b ON b.id=r.target ORDER BY r.id LIMIT 512)),
                  (SELECT COUNT(*) FROM shown),(SELECT COUNT(*) FROM permitted_edges),(SELECT COUNT(*) FROM picked)>?,
                  (SELECT json_group_array(kind) FROM (SELECT DISTINCT json_extract(v.metadata,'$.kind') AS kind FROM ranked s
                    JOIN versions v ON v.id=s.current_version JOIN documents d ON d.id=s.document_id
                    WHERE s.choice=1 AND d.availability='available' AND kind IS NOT NULL ORDER BY kind LIMIT 128)),
                  (SELECT json_group_array(type) FROM (SELECT DISTINCT type FROM permitted_relationships ORDER BY type LIMIT 128))""", (*args, *pick_args, limit)).fetchone()
                rows, edge_rows = json.loads(nodes_json), json.loads(edges_json)
                ids = [r["id"] for r in rows]
            else:
                total, total_edges, kinds, types, edge_json, focus_exists = db.execute(cte + """SELECT (SELECT COUNT(*) FROM shown),(SELECT COUNT(*) FROM permitted_edges),
              (SELECT json_group_array(kind) FROM (SELECT DISTINCT json_extract(v.metadata,'$.kind') AS kind
                FROM ranked s JOIN versions v ON v.id=s.current_version JOIN documents d ON d.id=s.document_id
                WHERE s.choice=1 AND d.availability='available' AND kind IS NOT NULL ORDER BY kind LIMIT 128)),
              (SELECT json_group_array(type) FROM (SELECT DISTINCT type FROM permitted_relationships ORDER BY type LIMIT 128)),
              (SELECT json_group_array(json_array(id,source,target,type)) FROM permitted_edges),
              EXISTS(SELECT 1 FROM shown WHERE id=?)""", (*args, focus)).fetchone()
                if not focus_exists:
                    raise ValueError("collection_graph_focus_not_in_scope")
                # Build bounded BFS from one consistent permitted edge read.
                # This does not create relationships or copy source bodies.
                edge_rows = [{"id": r[0], "source": r[1], "target": r[2], "type": r[3]} for r in json.loads(edge_json)]
                adjacency = {}
                for edge in edge_rows:
                    if edge["source"] != edge["target"]:
                        adjacency.setdefault(edge["source"], set()).add(edge["target"])
                        adjacency.setdefault(edge["target"], set()).add(edge["source"])
            facets = {"node_types": json.loads(kinds), "relations": json.loads(types)}
            if focus:
                ids = [focus]
                for _ in range(hops):
                    neighbors = sorted(set().union(*(adjacency.get(identifier, set()) for identifier in ids))-set(ids))[:min(limit,24)-len(ids)]
                    ids.extend(neighbors)
                    if not neighbors or len(ids) >= min(limit, 24):
                        break
                more = False
            if not ids:
                return {"ok": True, "nodes": [], "edges": [], "total_nodes": total, "total_edges": total_edges,
                        "next": None, "facets": facets, "activity_checkpoint": checkpoint}
            selected = ",".join("?" for _ in ids)
            if focus:
                rows = [{**dict(row), "degree": len(adjacency.get(row["id"], set()))} for row in db.execute(cte + "SELECT " + fields + " FROM visible s JOIN versions v ON v.id=s.visible_version WHERE s.id IN (" + selected + ") ORDER BY s.id", (*args, *ids))]
                selected_ids = set(ids)
                edge_rows = sorted((row for row in edge_rows if row["source"] in selected_ids and row["target"] in selected_ids), key=lambda row: row["id"])[:144]
            target_spaces = {}
            for association in db.execute("SELECT target_id,project FROM target_projects WHERE permission!='denied' AND project IN (" + marks + ") ORDER BY project", projects):
                target_spaces.setdefault(association["target_id"], []).append(association["project"])
            nodes = []
            for row in rows:
                meta = json.loads(row["metadata"]) if isinstance(row["metadata"], str) else row["metadata"]
                kind = meta.get("kind")
                kind = kind[:256] if isinstance(kind, str) and kind else "document"
                nodes.append({"id": row["id"], "label": row["label"], "category": kind,
                              "importance": 20, "updated_at": row["collected_at"], "description": row["body"][:2400],
                              "space": ", ".join(target_spaces.get(row["target_id"], [])), "source_version": row["visible_version"],
                              "source_url": row["source_url"], "source_target": row["target_id"], "source_platform": row["platform"],
                              "degree": row["degree"], "degree_scope": "permitted filtered graph",
                              "source_metadata": {"kind": kind}, "evidence": {"kind": "snapshot", "source_event_ids": [row["original_id"]], "chat_id": "", "confirmed_at": None, "retracted": False}})
            edges = [{"id": row["id"], "source": row["source"], "target": row["target"], "relation": row["type"], "context": "explicit source relationship", "weight": 1, "purpose": "reference", "evidence_message_id": row["id"], "evidence": {"kind": "snapshot", "source_event_ids": [row["id"]], "chat_id": "", "confirmed_at": None, "retracted": False}}
                     for row in edge_rows]
            result = {"ok": True, "nodes": nodes, "edges": edges, "total_nodes": total, "total_edges": total_edges,
                    "next": offset+limit if more else None, "focus": focus, "scope": projects,
                    "facets": facets, "targets": self.targets(projects),
                    "revision": (db.execute("SELECT e.sequence FROM events e WHERE EXISTS(SELECT 1 FROM target_projects p WHERE p.target_id=e.target_id AND p.permission!='denied' AND p.project IN (" + marks + ")) ORDER BY e.sequence DESC LIMIT 1", projects).fetchone() or [0])[0],
                    "display_scope": "bounded derived projection; not full graph", "activity_checkpoint": checkpoint}
            if details and focus:
                row = next(r for r in rows if r["id"] == focus)
                detail = self._record_details(db, row, expected_version)
                if detail["ok"] is not True:
                    return detail
                result["details"] = detail["details"]
            return result

    def _record_details(self, db, row, expected_version) -> dict:
        if expected_version is not None and row["visible_version"] != expected_version:
            return {"ok": False, "error": "collection_graph_version_changed"}
        raw = self._verified_source_blob(self.blobs, row["raw_path"], MAX_RECORD_BYTES)
        if digest(raw) != row["raw_sha256"]:
            raise RuntimeError("collection_source_integrity")
        body = row["body"] or json.dumps(json.loads(raw), ensure_ascii=False, indent=2)
        return {"ok": True, "details": {"node_id": row["id"], "basis": "source_record", "summary": row["label"],
                                     "body": body[:12000], "truncated": len(body) > 12000,
                                     "body_format": "normalized_text" if row["body"] else "retained_record_json",
                                     "version": row["visible_version"], "collected_at": row["collected_at"],
                                     "raw_sha256": row["raw_sha256"], "source_url": row["source_url"],
                                     "target_id": row["target_id"], "platform": row["platform"],
                                     "capture": self._source_capture_reference(db, row)}}

    @staticmethod
    def _verified_source_blob(folder: Path, name: str, budget: int) -> bytes:
        if not isinstance(name, str) or not re.fullmatch(r"[0-9a-f]{64}\.json", name):
            raise RuntimeError("collection_source_manifest_invalid")
        path = folder / name
        if any(part.is_symlink() for part in [path, *path.parents]):
            raise RuntimeError("collection_source_integrity")
        with path.open("rb") as handle:
            data = handle.read(budget + 1)
        if len(data) > budget or digest(data) + ".json" != name:
            raise RuntimeError("collection_source_integrity")
        return data

    def _source_capture_reference(self, db, row) -> dict | None:
        cursor = db.execute("SELECT cursor FROM targets WHERE id=?", (row["target_id"],)).fetchone()
        checkpoint = json.loads(cursor[0] or "{}")
        capture = checkpoint.get("source_capture") if isinstance(checkpoint, dict) else None
        if not isinstance(capture, dict):
            return None
        manifest = json.loads(self._verified_source_blob(self.root / "source-captures", capture.get("manifest"), 16 * 1024 * 1024))
        if manifest.get("target_id") != row["target_id"] or manifest.get("source_sha256") != capture.get("sha256"):
            raise RuntimeError("collection_source_integrity")
        entry = next((r for r in manifest["records"] if r["document_id"] == row["id"] and r["version"] == row["visible_version"]), None)
        if entry is None:
            return None  # The saved file receipt predates this record revision.
        if entry.get("record_sha256") != row["raw_sha256"]:
            raise RuntimeError("collection_source_integrity")
        return {"status": "manifest_verified", "json_pointer": entry["json_pointer"],
                "source_sha256": manifest["source_sha256"], "source_bytes": manifest["source_bytes"],
                "byte_scope": manifest["byte_scope"]}

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

    @staticmethod
    def _activity_scope(db, projects, target_id=None, platform=None):
        first = db.execute("SELECT event_id FROM events ORDER BY sequence LIMIT 1").fetchone()
        generation = identity("stream", str(SCHEMA) + ":" + (first[0] if first else "empty"))
        marks = ",".join("?" for _ in projects) or "NULL"
        # Materialize the small permitted target set once, rather than running
        # a correlated permission lookup for every retained journal stage.
        where = """e.target_id IN (SELECT p.target_id FROM target_projects p
          JOIN targets t ON t.id=p.target_id WHERE p.permission!='denied'
          AND p.project IN (""" + marks + """ ) AND (? IS NULL OR p.target_id=?)
          AND (? IS NULL OR t.platform=?))"""
        args = [*projects, target_id, target_id, platform, platform]
        earliest, latest = db.execute("SELECT COALESCE(MIN(e.sequence),0),COALESCE(MAX(e.sequence),0) FROM events e JOIN targets t ON t.id=e.target_id WHERE " + where, args).fetchone()
        return {"stream_id": generation, "cursor": latest}, earliest, where, args

    def activity_page(self, *, projects: list[str], after=None, stream_id=None, limit=200,
                      target_id=None, platform=None) -> dict:
        """Committed journal receipts, never inferred knowledge/model activity.

        The cursor advances over every permitted stage. Only actual changed
        records from completed runs become visual candidates. A baseline or
        replaced/rewound journal requires a graph snapshot, not old pulses.
        """
        if (type(limit) is not int or not 1 <= limit <= 200
            or after is not None and (type(after) is not int or after < 0)
            or stream_id is not None and (not isinstance(stream_id, str) or len(stream_id) > 128)):
            raise ValueError("collection_activity_cursor_invalid")
        if target_id is not None and (not isinstance(target_id, str) or len(target_id) > 256):
            raise ValueError("collection_graph_filter_invalid")
        if platform is not None and platform not in {"youtube", "threads", "files", "graph"}:
            raise ValueError("collection_platform_invalid")
        with self.database() as db:
            checkpoint, earliest, where, args = self._activity_scope(db, projects, target_id, platform)
            generation, latest = checkpoint["stream_id"], checkpoint["cursor"]
            # Sequence gaps also belong to other permitted/denied targets.
            # They are not pruning evidence. The retained first global event
            # identifies replacement/pruning; a rewind is checked separately.
            reset = after is None or stream_id != generation or after > latest
            if reset:
                return {"ok": True, "items": [], "cursor": latest, "latest": latest,
                        "stream_id": generation, "reset": True, "has_more": False}
            rows = db.execute("""SELECT e.*,r.origin,r.state AS run_state FROM events e
              JOIN targets t ON t.id=e.target_id JOIN runs r ON r.id=e.run_id WHERE """ + where +
              " AND e.sequence>? ORDER BY e.sequence LIMIT ?", (*args, after, limit+1)).fetchall()
            items = []
            for row in rows[:limit]:
                details = json.loads(row["details"])
                if (row["stage"] != "stored" or row["run_state"] != "complete"
                    or not row["document_id"] or details.get("change") not in {"added", "revised"}):
                    continue
                items.append({"sequence": row["sequence"], "event_id": row["event_id"],
                              "document_id": row["document_id"], "version": row["version"],
                              "at": row["at"], "target_id": row["target_id"], "run_id": row["run_id"],
                              "origin": row["origin"], "kind": details["change"], "success": True})
            return {"ok": True, "items": items, "cursor": rows[min(len(rows),limit)-1]["sequence"] if rows else after,
                    "latest": latest, "stream_id": generation, "reset": False, "has_more": len(rows)>limit}


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
        if options.get("activity") is True:
            return store.activity_page(projects=projects, after=options.get("after"), stream_id=options.get("stream_id"),
                                       limit=options.get("limit",200), target_id=options.get("target_id"), platform=options.get("platform"))
        return store.graph_page(projects=projects, limit=options.get("limit",120), offset=options.get("offset",0),
                                focus=options.get("focus"), hops=options.get("hops",1), query=str(options.get("search", ""))[:256],
                                target_id=options.get("target_id"), platform=options.get("platform"),
                                node_type=options.get("node_type"), relation=options.get("relation"),
                                since=options.get("since"), until=options.get("until"),
                                details=options.get("details",False), expected_version=options.get("expected_version"))
    raise ValueError("collection_action_invalid")
