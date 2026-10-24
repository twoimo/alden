"""Versioned multi-target collection journal and derived logical graph.

Original stores remain authoritative. This private store retains source bytes,
identity mappings and stage receipts; it never sends messages or infers edges.
"""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
from alden_abort import AldenCancelled
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
from alden_source_text import project_source_text, reading_tail_presentation


SCHEMA = 3
PLATFORMS = frozenset({'threads', 'youtube', 'files', 'graph', 'kakaotalk', 'telegram'})
_GRAPH_REFERENCES = object()  # Internal capability, never a JSON query flag.
SNAPSHOT_RECORD_BUDGET = 100000
MAX_RECORD_BYTES = 2 * 1024 * 1024
MAX_BATCH_RECORDS = 2000
BODY_NORMALIZATION_VERSION = "body-nfc-lines-v2"
STAGES = {"discovered", "parsed", "validated", "stored", "published", "indexed", "failed", "paused"}


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


_GENERIC_OVERVIEW_LABELS = {'snippets', 'sections', 'config', 'templates', 'assets', 'layout', 'locales', 'src', 'dist', 'node_modules', 'index', 'readme'}
_ASSET_OVERVIEW_LABEL = re.compile(r'(?i)^[A-Za-z0-9_.-]{1,80}[.](?:liquid|json|js|css|scss|svg|md)$')
_DISPLAY_LINK_LINE = re.compile(
    r'(?:https?://\S+|(?:[-*•]\s*)?(?:링크|원문(?:\s*링크)?|URL)\s*:\s*https?://\S+)', re.I)
_DISPLAY_REACTION_LINE = re.compile(
    r'(?:[-*•]\s*)?반응:\s*좋아요\s*[0-9][0-9,]*\s*·\s*답글\s*[0-9][0-9,]*\s*·\s*리포스트\s*[0-9][0-9,]*')


def overview_label_tier(label) -> int:
    """Rank saved labels for the overview sample. 0 is a readable subject.

    Empty labels and bare asset or directory names stay in the store and in
    search. They only lose the first slots of an unfiltered overview.
    """
    text = label.strip() if isinstance(label, str) else ''
    if not text or not re.search(r'[A-Za-z가-힣]', text):
        return 2
    folded = text.casefold()
    if folded in _GENERIC_OVERVIEW_LABELS or _ASSET_OVERVIEW_LABEL.fullmatch(text):
        return 1
    return 0


def _overview_text_lines(body):
    """Saved content lines shared by display titles and overview admission."""
    for raw in body.splitlines():
        line = ' '.join(raw.split())
        if not line or re.fullmatch(r'\d+\s*시간', line) or re.fullmatch(r'@?[A-Za-z0-9_.]+', line):
            continue
        if _DISPLAY_LINK_LINE.fullmatch(line) or _DISPLAY_REACTION_LINE.fullmatch(line):
            continue
        yield line


def overview_display_label(label, body=None) -> str:
    """Show a saved title, or the first substantive line when the title is blank.

    This does not write the source version. Channel handles and relative times
    are skipped. The result is a display string, not a new identity.
    """
    text = label.strip() if isinstance(label, str) else ''
    if text:
        return text
    if not isinstance(body, str):
        return ''
    short_korean = english = ''
    for line in _overview_text_lines(body):
        if re.search(r'[가-힣]', line):
            if len(line) >= 12:
                return line[:80]
            if not short_korean and len(line) >= 2:
                short_korean = line
        if not english and re.search(r'[A-Za-z]', line) and len(line) >= 12:
            english = line
    return (short_korean or english)[:80]



def overview_sample_tier(label, kind) -> int:
    """Overview sampling only. Code paths and link-only lines stay stored."""
    text = label.strip() if isinstance(label, str) else ''
    folded = text.replace('\\', '/')
    path_like = '/' in folded or bool(re.search(r'\.[A-Za-z0-9]{1,8}$', text))
    if not re.search(r'[가-힣]', text) and (kind in {'hub', 'code'} or path_like):
        return 3
    if re.search(r'https?://', text, re.I):
        remainder = re.sub(r'https?://\S+', ' ', text, flags=re.I)
        remainder = re.sub(r'(?i)^[\s\-•]*링크\s*[:：]?\s*', '', remainder)
        letters = re.findall(r'[A-Za-z가-힣]', remainder)
        if len(letters) < 8:
            return 3
    if text in {'기타', '미분류', '기타·미분류', '일반 동영상', '전체 동영상', '동영상', '영상', '홍보·안내', '외부 자료·링크'}:
        return 3
    if kind == 'source_group':
        return 3
    if re.fullmatch(r'[A-Za-z0-9_-]{6,32}', text):
        return 3
    return overview_label_tier(label)


def overview_sample_rank(label, body, kind) -> int:
    """Overview order when the stored title and body are both available.

    A short untitled post stays stored without filling the overview. Count
    substantive saved lines across the body, not just its first display line;
    export headers and numeric counters do not make a short post substantive.
    """
    shown = overview_display_label(label if isinstance(label, str) else '', body if isinstance(body, str) else '')
    named = kind if isinstance(kind, str) else ''
    tier = overview_sample_tier(shown, named)
    if tier >= 3:
        return tier
    stored = label.strip() if isinstance(label, str) else ''
    if not stored and named == 'source':
        size = 0
        for line in _overview_text_lines(body if isinstance(body, str) else ''):
            if re.search(r'[A-Za-z가-힣]', line):
                size += len(line)
            if size >= 40:
                break
        if size < 40:
            return 3
    return tier


def overview_routing_hub(node) -> bool:
    """A saved bucket can stay in the graph without being an overview attractor.

    File names, generic media buckets and one-word source groups pull unrelated
    notes into one star. They remain stored and available in detail views.
    """
    label = node.get("label") if isinstance(node, dict) else ""
    text = label.strip() if isinstance(label, str) else ""
    if text in _GENERIC_OVERVIEW_LABELS or text in {
        "기타", "미분류", "기타·미분류", "일반 동영상", "전체 동영상", "동영상", "영상",
        "홍보·안내", "외부 자료·링크",
    } or overview_label_tier(text) >= 1:
        return True
    if node.get("category") in {"hub", "code"} and not re.search(r"[가-힣]", text):
        return True
    if node.get("category") != "source_group":
        return False
    terms = [term for term in re.split(r"[·\s/]+", text) if re.search(r"[A-Za-z가-힣]{2}", term)]
    return len(terms) < 2


def overview_draw_edges(edges, nodes):
    """Overview lines only. Stored relations and non-overview pages are unchanged."""
    hidden = {node.get("id") for node in nodes if overview_routing_hub(node)}
    hidden.discard(None)
    return [edge for edge in edges if edge.get("source") not in hidden and edge.get("target") not in hidden]


def normalized_body(value: str) -> str:
    # Indentation, tabs, blank lines and Markdown hard breaks carry meaning.
    # Original bytes stay in the source blob; only Unicode composition and
    # platform line endings are normalized in the searchable projection.
    return unicodedata.normalize("NFC", value).replace("\r\n", "\n").replace("\r", "\n")


def korean_literal_plan(query):
    """Positive literal AND/OR only; advanced FTS keeps its original meaning."""
    if not isinstance(query, str) or len(query) > 1024:
        return None
    tokens = re.findall(r'"(?:[^"]|"")*"|[^\s]+', unicodedata.normalize('NFC', query))
    clauses, expect_word, pending = [[]], True, 'AND'
    words = 0
    for token in tokens:
        if token in ('AND', 'OR'):
            if expect_word:
                return None
            pending, expect_word = token, True
            continue
        word = token[1:-1] if token.startswith('"') and token.endswith('"') else token
        if not re.fullmatch(r'[0-9A-Za-z가-힣]{2,64}', word) or not re.search(r'[가-힣]', word):
            return None
        if pending == 'OR':
            clauses.append([])
        clauses[-1].append(word.lower())
        pending, expect_word = 'AND', False
        words += 1
        if words > 24:
            return None
    return tuple(tuple(c) for c in clauses) if words and not expect_word else None


def korean_literal_sql(plan, label='v.label', body='v.body', *, numeric_boundaries=False):
    # Column expressions are fixed by callers. All query values stay bound.
    clauses, values = [], []
    for clause in plan:
        expressions = []
        for word in clause:
            if numeric_boundaries and (word[0].isdigit() or word[-1].isdigit()):
                expressions.append(f'alden_korean_literal({label},{body},?)=1')
                values.append(word)
                continue
            columns = [f'lower({c})' if re.search(r'[a-z]', word) else c for c in (label, body)]
            expressions.append(f'(instr({columns[0]},?)>0 OR instr({columns[1]},?)>0)')
            values.extend((word, word))
        clauses.append('(' + ' AND '.join(expressions) + ')')
    return '(' + ' OR '.join(clauses) + ')', tuple(values)


def korean_literal_matches(plan, label, body):
    fields = [unicodedata.normalize('NFC', value).lower() for value in (label, body)]
    def contains(field, word):
        offset = field.find(word)
        while offset >= 0:
            end = offset + len(word)
            left = not word[0].isdigit() or offset == 0 or not field[offset-1].isdigit()
            right = not word[-1].isdigit() or end == len(field) or not field[end].isdigit()
            if left and right:
                return True
            offset = field.find(word, offset + 1)
        return False
    return any(all(any(contains(field, word) for field in fields) for word in clause) for clause in plan)


RELATION_BIND_FIELDS = ('target_id', 'source', 'target', 'type', 'evidence')
# Fixed aliases are used by the bounded graph and read-only quality queries.
RELATION_HISTORY_MATCH = ('h.relation_id IS NOT NULL AND '
    + ' AND '.join('h.'+name+' IS r.'+name for name in RELATION_BIND_FIELDS))


def relation_evidence_digest(value):
    if not isinstance(value, str): return None
    try:
        return digest(value.encode())
    except UnicodeError:
        return None


def require_relation_binding(db, relation, *, allow_new=False):
    """Check revision consistency, not original identity or semantic truth."""
    try:
        values = tuple(relation[name] for name in RELATION_BIND_FIELDS)
        if not isinstance(values[-1], str) or digest(values[-1].encode()) != relation['version']:
            raise RuntimeError('collection_relation_integrity')
        previous = db.execute('''SELECT target_id,source,target,type,evidence FROM relation_versions
            WHERE relation_id=? AND version=?''', (relation['id'], relation['version'])).fetchone()
        if previous is None:
            if allow_new: return
            raise RuntimeError('collection_relation_integrity')
        if tuple(previous) != values:
            raise RuntimeError('collection_relation_integrity')
    except (KeyError, IndexError, TypeError, UnicodeError, sqlite3.Error) as error:
        raise RuntimeError('collection_relation_integrity') from error


def same_file_relation_evidence(previous, incoming, source_revision):
    """An unchanged file re-read is not a changed relationship assertion."""
    try:
        old = json.loads(previous)
        if not isinstance(old, dict) or encoded(old).decode() != previous or not isinstance(incoming, dict):
            return False
        a, b = old.get('source'), incoming.get('source')
        if not isinstance(a, dict) or not isinstance(b, dict):return False
        if a.get('sha256') != source_revision or b.get('sha256') != source_revision:return False
        if any(type(s.get('observed_at')) not in (int,float) or not math.isfinite(s['observed_at']) or s['observed_at']<=0 for s in (a,b)):
            return False
        stripped = lambda value: {**value, 'source': {k:v for k,v in value['source'].items() if k != 'observed_at'}}
        return stripped(old) == stripped(incoming)
    except (ValueError,TypeError,UnicodeError,RecursionError):
        return False


def source_observation(cursor, source_revision):
    if not isinstance(cursor, dict) or 'source_observation' not in cursor:return None
    value=cursor['source_observation']
    if (not isinstance(value,dict) or set(value)!= {'source_revision','observed_at'}
            or value['source_revision']!=source_revision
            or type(value['observed_at']) not in (int,float) or abs(value['observed_at'])>2**53
            or not math.isfinite(value['observed_at']) or value['observed_at']<=0):
        raise ValueError('collection_source_observation_invalid')
    return {**value,'observed_at':float(value['observed_at'])}


SOURCE_OBSERVATIONS_SQL = '''CREATE TABLE IF NOT EXISTS source_observations(
    id TEXT PRIMARY KEY,target_id TEXT NOT NULL,source_revision TEXT NOT NULL,
    processing_version TEXT NOT NULL,projection_sha256 TEXT NOT NULL,
    observed_at REAL NOT NULL,confirmed_at REAL NOT NULL,basis_run_id TEXT NOT NULL,
    record_count INTEGER NOT NULL,relation_count INTEGER NOT NULL,
    FOREIGN KEY(target_id) REFERENCES targets(id),FOREIGN KEY(basis_run_id) REFERENCES runs(id))'''


def record_source_observation(db,target_id,revision,processor,projection,observation,run_id,records,relations):
    if observation is None:return None
    if not db.execute("SELECT 1 FROM runs WHERE id=? AND target_id=? AND state='complete'",(run_id,target_id)).fetchone():
        raise RuntimeError('collection_observation_binding')
    db.execute(SOURCE_OBSERVATIONS_SQL)
    value=(target_id,revision,processor,projection,observation['observed_at'],run_id,records,relations)
    key=identity('source-observation',encoded(value).decode())
    old=db.execute('''SELECT target_id,source_revision,processing_version,projection_sha256,
        observed_at,basis_run_id,record_count,relation_count FROM source_observations WHERE id=?''',(key,)).fetchone()
    if old is not None and tuple(old)!=value:raise RuntimeError('collection_observation_binding')
    db.execute('INSERT OR IGNORE INTO source_observations VALUES(?,?,?,?,?,?,?,?,?,?)',
               (key,*value[:5],time.time(),*value[5:]))
    return key


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


def require_collection_source_root(state_root, *callbacks):
    """Enforce the explicit root contract of participating Python adapters.

    Legacy trusted callables without a declaration retain their old contract;
    they are not thereby certified as bound to this root. JSON cannot create
    a callback or supply this declaration.
    """
    expected = Path(state_root).absolute()
    for callback in callbacks:
        owner = getattr(callback, '__self__', None)
        declaration = getattr(owner, 'collection_source_root', None)
        if declaration is None:
            continue
        try:
            root = declaration()
            if not isinstance(root, Path) or root.absolute() != expected:
                raise ValueError('root')
        except Exception as error:
            raise ValueError('collection_source_root_mismatch') from error


class CollectionStore:
    def __init__(self, state_root: Path, *, source_access=None, source_revision=None):
        require_collection_source_root(state_root, source_access, source_revision)
        self.source_access = source_access
        self.source_revision = source_revision
        self.read_only = False
        self.schema = SCHEMA
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
            if prior and int(prior[0]) not in {1, 2, SCHEMA}:
                raise RuntimeError("collection_schema_requires_migration")
            journal = db.execute("SELECT value FROM meta WHERE key='journal_id'").fetchone() if has_meta else None
            counts = db.execute("SELECT value FROM meta WHERE key='journal_counts_v1'").fetchone() if has_meta else None
            if prior and (int(prior[0]) < SCHEMA or journal is None or counts is None):
                # Only this derived store is migrated. Retain a consistent,
                # private recovery copy before changing its schema.
                backup = self.root / ("collection.schema-" + prior[0] + "-" + uuid.uuid4().hex + ".sqlite3")
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
                  collected_at REAL NOT NULL,processing_version TEXT NOT NULL DEFAULT 'legacy',
                  projection_sha256 TEXT NOT NULL DEFAULT '',
                  UNIQUE(document_id,raw_sha256,processing_version,projection_sha256),
                  FOREIGN KEY(document_id) REFERENCES documents(id));
                CREATE TABLE IF NOT EXISTS memberships(
                  target_id TEXT NOT NULL,document_id TEXT NOT NULL,current_version TEXT,
                  availability TEXT NOT NULL DEFAULT 'available',
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
                CREATE TABLE IF NOT EXISTS target_snapshots(
                  target_id TEXT PRIMARY KEY,source_revision TEXT NOT NULL,source_order INTEGER NOT NULL,
                  processing_version TEXT NOT NULL,projection_sha256 TEXT NOT NULL,run_id TEXT NOT NULL,
                  FOREIGN KEY(target_id) REFERENCES targets(id));
                CREATE TABLE IF NOT EXISTS relation_versions(
                  relation_id TEXT NOT NULL,version TEXT NOT NULL,target_id TEXT NOT NULL,
                  source TEXT NOT NULL,target TEXT NOT NULL,type TEXT NOT NULL,evidence TEXT NOT NULL,
                  at REAL NOT NULL,PRIMARY KEY(relation_id,version));
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
            if version and int(version[0]) < SCHEMA:
                columns = {r[1] for r in db.execute('PRAGMA table_info(versions)')}
                if 'processing_version' not in columns:
                    db.execute('''CREATE TABLE versions_v3(
                        id TEXT PRIMARY KEY,document_id TEXT NOT NULL,raw_sha256 TEXT NOT NULL,
                        raw_path TEXT NOT NULL,label TEXT NOT NULL,body TEXT NOT NULL,metadata TEXT NOT NULL,
                        collected_at REAL NOT NULL,processing_version TEXT NOT NULL DEFAULT 'legacy',
                        projection_sha256 TEXT NOT NULL DEFAULT '',
                        UNIQUE(document_id,raw_sha256,processing_version,projection_sha256),
                        FOREIGN KEY(document_id) REFERENCES documents(id))''')
                    db.execute("INSERT INTO versions_v3 SELECT *, 'legacy', '' FROM versions")
                    db.execute('DROP TABLE versions')
                    db.execute('ALTER TABLE versions_v3 RENAME TO versions')
                if 'availability' not in {r[1] for r in db.execute('PRAGMA table_info(memberships)')}:
                    db.execute("ALTER TABLE memberships ADD COLUMN availability TEXT NOT NULL DEFAULT 'available'")
                db.execute('''INSERT OR IGNORE INTO relation_versions
                    SELECT id,version,target_id,source,target,type,evidence,? FROM relations''', (time.time(),))
            db.execute("INSERT INTO meta VALUES('schema',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(SCHEMA),))
            # Identity precedes the first event and survives reopen. Read-only
            # lookup never initializes it; legacy stores retain their old ID
            # until their next ordinary writer opens this backed-up store.
            db.execute("INSERT OR IGNORE INTO meta VALUES('journal_id',?)", (uuid.uuid4().hex,))
            db.execute("INSERT OR IGNORE INTO meta VALUES('journal_first_event',COALESCE((SELECT event_id FROM events ORDER BY sequence LIMIT 1),''))")
            # Optional schema-3 accelerator: old writers also execute these
            # triggers, so exact counts share commit/rollback and deduplication
            # with the journal. No model or source record is synthesized.
            db.execute('''CREATE TABLE IF NOT EXISTS event_counts(
                target_id TEXT NOT NULL,stage TEXT NOT NULL,change TEXT NOT NULL,count INTEGER NOT NULL,
                PRIMARY KEY(target_id,stage,change))''')
            if db.execute("SELECT 1 FROM meta WHERE key='journal_counts_v1'").fetchone() is None:
                db.execute('''INSERT INTO event_counts
                    SELECT target_id,stage,CASE WHEN stage='stored' THEN COALESCE(json_extract(details,'$.change'),'') ELSE '' END,COUNT(*)
                    FROM events GROUP BY 1,2,3''')
                db.execute("INSERT INTO meta VALUES('journal_counts_v1','1')")
            increment = """INSERT INTO event_counts VALUES(NEW.target_id,NEW.stage,
                CASE WHEN NEW.stage='stored' THEN COALESCE(json_extract(NEW.details,'$.change'),'') ELSE '' END,1)
                ON CONFLICT(target_id,stage,change) DO UPDATE SET count=count+1;"""
            decrement = """UPDATE event_counts SET count=count-1 WHERE target_id=OLD.target_id AND stage=OLD.stage
                AND change=CASE WHEN OLD.stage='stored' THEN COALESCE(json_extract(OLD.details,'$.change'),'') ELSE '' END;
                DELETE FROM event_counts WHERE count=0;"""
            db.execute('CREATE TRIGGER IF NOT EXISTS event_count_insert AFTER INSERT ON events BEGIN ' + increment +
                       " UPDATE meta SET value=NEW.event_id WHERE key='journal_first_event' AND value=''; END")
            db.execute('CREATE TRIGGER IF NOT EXISTS event_count_delete AFTER DELETE ON events BEGIN ' + decrement + ' END')
            db.execute('CREATE TRIGGER IF NOT EXISTS event_count_update AFTER UPDATE ON events BEGIN ' + decrement + increment + ' END')
            db.execute('CREATE INDEX IF NOT EXISTS event_target_sequence ON events(target_id,sequence)')

    @classmethod
    def open_existing(cls, state_root: Path, *, source_access=None, source_revision=None):
        require_collection_source_root(state_root, source_access, source_revision)
        path = state_root.absolute() / "knowledge" / "collection" / "collection.sqlite3"
        if not path.exists():
            return None
        if any(p.is_symlink() for p in [*path.parents, path]):
            raise ValueError("collection_path_symlink")
        store = cls.__new__(cls)
        store.source_access = source_access
        store.source_revision = source_revision
        store.read_only = True
        store.path, store.root = path, path.parent
        store.blobs, store.locks = store.root / "sources", store.root / "locks"
        with store.database() as db:
            row = db.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
            if row is None or int(row[0]) not in {2, SCHEMA}:
                raise RuntimeError("collection_schema_requires_migration")
            store.schema = int(row[0])
        return store

    def _install_source_access(self, db, *, cancelled=None):
        protected, decisions, scopes = None, {}, {}
        def check_cancelled():
            if cancelled is not None and cancelled():
                raise RuntimeError('collection_cancelled')
        def guarded_targets():
            nonlocal protected
            if protected is None:
                protected = {row[0] for row in db.execute(
                    "SELECT id FROM targets WHERE platform IN ('kakaotalk','telegram')")}
            return protected
        def check_source(key, metadata):
            check_cancelled()
            try:
                return callable(self.source_access) and self.source_access(*key, json.loads(metadata)) is True
            except Exception:
                return False
        def current_scope(target):
            check_cancelled()
            revision = getattr(self, 'source_revision', None)
            if revision is None:
                return 'unversioned' if callable(getattr(self, 'source_access', None)) else 'withheld'
            if not callable(revision):
                return 'unavailable'
            try:
                value = revision(target)
                return value if isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value) else 'unavailable'
            except Exception:
                return 'unavailable'
        def source_scope(target):
            if target not in guarded_targets():
                return None
            if target not in scopes:
                scopes[target] = current_scope(target)
            return scopes[target]
        def source_allowed(target, document, version):
            check_cancelled()
            if target not in guarded_targets():
                return 1
            if source_scope(target) == 'unavailable':
                return 0
            key = (target, document, version)
            if key not in decisions:
                row = db.execute('SELECT metadata FROM versions WHERE id=? AND document_id=?', (version, document)).fetchone()
                metadata = row[0] if row else '{}'
                decisions[key] = (metadata, bool(row) and check_source(key, metadata))
            return int(decisions[key][1])
        db.create_function('alden_source_allowed', 3, source_allowed)
        db.create_function('alden_has_source_guards', 0, lambda: int(bool(guarded_targets())))
        db.create_function('alden_source_scope', 1, source_scope)
        def verify():
            check_cancelled()
            for target, value in scopes.items():
                if current_scope(target) != value:
                    raise RuntimeError('collection_source_authorization_changed')
            for key, (metadata, allowed) in decisions.items():
                check_cancelled()
                if allowed and not check_source(key, metadata):
                    raise RuntimeError('collection_source_authorization_changed')
        return verify

    @contextmanager
    def database(self, *, publication_guard=None, cancelled=None):
        require_collection_source_root(self.path.parents[2], self.source_access, self.source_revision)
        db = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=.15) if self.read_only else sqlite3.connect(self.path, timeout=.15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        verify_source_access = self._install_source_access(db, cancelled=cancelled)
        if self.read_only:
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
        else:
            # The default ~2 MiB page cache thrashes during the bounded full
            # source transaction. This applies only to the private derived
            # writer, never to an original database or foreground reader.
            db.execute('PRAGMA cache_size=-65536')
        try:
            if cancelled is not None:
                db.set_progress_handler(lambda: int(bool(cancelled())), 256)
                if cancelled():
                    raise RuntimeError('collection_cancelled')
            yield db
            require_collection_source_root(self.path.parents[2], self.source_access, self.source_revision)
            verify_source_access()
            with publication_guard() if publication_guard is not None else nullcontext():
                if cancelled is not None and cancelled():
                    raise RuntimeError('collection_cancelled')
                db.commit()
        except BaseException as error:
            # An interrupted VM must not also interrupt its rollback.
            db.set_progress_handler(None, 0)
            db.rollback()
            if isinstance(error, sqlite3.Error) and cancelled is not None and cancelled():
                raise RuntimeError('collection_cancelled') from error
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
        if platform not in PLATFORMS:
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

    def update_interval(self, target_id: str, seconds: int, expected: int) -> dict:
        if (type(seconds) is not int or not 60 <= seconds <= 30 * 86400
                or type(expected) is not int or not 60 <= expected <= 30 * 86400):
            raise ValueError('collection_interval_invalid')
        with self.target_lock(target_id), self.database() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('''SELECT t.id,t.interval_seconds,t.next_run,t.last_success FROM targets t
                WHERE t.id=? AND EXISTS(SELECT 1 FROM target_projects p WHERE p.target_id=t.id AND p.permission!='denied')''', (target_id,)).fetchone()
            if row is None: raise ValueError('collection_target_missing_or_denied')
            if row['interval_seconds'] != expected: raise ValueError('collection_interval_changed')
            if seconds == expected: return {'state': 'unchanged', 'target_id': target_id}
            raw = encoded(dict(row))
            folder = safe_directory(self.root / 'schedule-backups')
            path = folder / (digest(raw) + '.json')
            if not path.exists():
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
                with os.fdopen(fd, 'wb') as handle:
                    handle.write(raw); handle.flush(); os.fsync(handle.fileno())
            if path.is_symlink() or path.read_bytes() != raw:
                raise RuntimeError('collection_schedule_backup_mismatch')
            now = time.time()
            due = row['next_run'] is None or row['next_run'] <= now
            next_run = (row['next_run'] if row['next_run'] is not None else now) if due else max(now, (row['last_success'] or now) + seconds)
            db.execute('UPDATE targets SET interval_seconds=?,next_run=? WHERE id=?', (seconds, next_run, target_id))
            return {'state': 'saved', 'target_id': target_id, 'interval_seconds': seconds, 'next_run': next_run}

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
                available = " AND availability='available'" if self.schema >= 3 else ""
                saved = {r["document_id"]: r["current_version"] for r in db.execute(
                    "SELECT document_id,current_version FROM memberships WHERE target_id=?" + available, (target_id,))}
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
            with self.database() as db:
                current = db.execute("SELECT cursor FROM targets WHERE id=?", (target_id,)).fetchone()
                if current[0] != expected_cursor:
                    raise RuntimeError("collection_source_checkpoint_changed")
                snapshot = db.execute("SELECT run_id FROM target_snapshots WHERE target_id=?",
                                      (target_id,)).fetchone()
                # A repeated source is new evidence for the current committed
                # transition. Archive bytes/manifests remain content-addressed.
                run_id = identity("source-capture", encoded([
                    target_id, manifest_sha, snapshot[0] if snapshot else None]).decode())
                if cancelled():
                    raise RuntimeError("collection_cancelled")
                cursor = json.loads(expected_cursor)
                cursor["source_capture"] = {"schema": 1, "manifest": manifest_name,
                                            "sha256": sha, "byte_scope": manifest["byte_scope"]}
                previous = db.execute("SELECT 1 FROM runs WHERE id=? AND state='complete'", (run_id,)).fetchone()
                if previous:
                    # Repair an absent reference using already verified archive
                    # evidence without rewriting the original run or its event.
                    db.execute("UPDATE targets SET cursor=? WHERE id=?", (encoded(cursor).decode(), target_id))
                    return {"state": "unchanged", "run_id": run_id, "manifest": manifest_name,
                            "sha256": sha, "checkpoint_restored": True}
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
        key = [run_id, stage, document_id, version]
        if details.get('change') == 'relations_changed':
            key.append('relations_changed')
        event_id = identity("event", encoded(key).decode())
        db.execute("INSERT OR IGNORE INTO events(event_id,run_id,target_id,stage,at,document_id,version,details) VALUES(?,?,?,?,?,?,?,?)",
                   (event_id, run_id, target_id, stage, time.time(), document_id, version, encoded(details).decode()))
        db.execute("UPDATE meta SET value=(SELECT event_id FROM events ORDER BY sequence LIMIT 1) WHERE key='journal_first_event' AND value=''")

    def ingest(self, target_id: str, records, *, cursor=None, origin="alden",
               run_id: str | None = None, relations=(), cancelled=lambda: False,
               complete_snapshot=False, source_revision=None, source_order=None,
               processing_version="collection-record-v1", publication_guard=None, retractions=(),
               defer_activity=False) -> dict:
        if type(defer_activity) is not bool:
            raise ValueError('collection_activity_defer_invalid')
        activity_details = {'activity_deferred': True} if defer_activity else {}
        retractions = tuple(retractions)
        if len(retractions) > MAX_BATCH_RECORDS or complete_snapshot and retractions:
            raise ValueError('collection_retractions_invalid')
        retracted_ids = []
        for record in retractions:
            if not isinstance(record, dict) or set(record) != {'platform', 'original_id'} or record['platform'] not in PLATFORMS:
                raise ValueError('collection_retractions_invalid')
            retracted_ids.append(identity(record['platform'], record['original_id']))
        if len(set(retracted_ids)) != len(retracted_ids):
            raise ValueError('collection_retractions_invalid')
        if retracted_ids:
            records = list(records)
            if len(records) > MAX_BATCH_RECORDS:
                raise ValueError('collection_retractions_invalid')
            target_platform = self.target(target_id)['platform']
            if any(identity(record.get('platform', target_platform), record['original_id']) in retracted_ids for record in records):
                raise ValueError('collection_retraction_conflicts_with_record')
        automatic_snapshot_run = complete_snapshot and not run_id
        if not isinstance(processing_version, str) or not 1 <= len(processing_version) <= 128:
            raise ValueError("collection_processing_version_invalid")
        if complete_snapshot:
            if not isinstance(source_revision, str) or not re.fullmatch(r'[0-9a-f]{64}', source_revision) or type(source_order) is not int or source_order < 0:
                raise ValueError("collection_source_revision_invalid")
            records, relations = list(records), list(relations)
            observation = source_observation(cursor, source_revision)
            if len(records) > SNAPSHOT_RECORD_BUDGET:
                raise ValueError("collection_snapshot_budget")
            target_platform = self.target(target_id)['platform']
            keys = [identity(r.get('platform', target_platform), r['original_id']) for r in records]
            if len(keys) != len(set(keys)):
                raise ValueError("collection_duplicate_original_id")
            projection = digest(encoded([
                [{k:v for k,v in r.items() if k != 'source'} for r in records],
                [{**r, 'evidence': {k:v for k,v in r.get('evidence',{}).items() if k != 'source'}} for r in relations],
                BODY_NORMALIZATION_VERSION]))
            # Bind the store's projection policy independently of the caller's
            # parser version. Old unchanged snapshots must be visited once;
            # document versions still change only with their actual projection.
            run_id = run_id or identity('snapshot-run', encoded([target_id, source_revision, processing_version, projection]).decode())
        else:
            projection = None
        run_id = run_id or uuid.uuid4().hex
        with self.target_lock(target_id):
            target = self.target(target_id)
            if not target["enabled"]:
                raise RuntimeError("collection_target_paused")
            if (complete_snapshot or retracted_ids) and not any(p['permission'] != 'denied' for p in target['projects']):
                raise ValueError('collection_source_scope_denied')
            counts = {"added": 0, "revised": 0, "unchanged": 0}
            with self.database() as db:
                if complete_snapshot:
                    prior = db.execute('SELECT * FROM target_snapshots WHERE target_id=?', (target_id,)).fetchone()
                    effective_order = max(source_order, prior['source_order']) if prior and prior['source_revision'] == source_revision else source_order
                    if prior and prior['source_revision'] != source_revision and source_order <= prior['source_order']:
                        raise RuntimeError('collection_source_revision_stale' if source_order < prior['source_order'] else 'collection_source_revision_conflict')
                    if prior and prior['source_revision'] == source_revision and prior['processing_version'] == processing_version and prior['projection_sha256'] == projection:
                        # Unchanged content still confirms its source order;
                        # retain the maximum so a delayed revision cannot win.
                        # The outer connection has only read metadata. Publish
                        # confirmation under the same fence as changed content,
                        # without rewriting the completed run or its history.
                        if cancelled():
                            raise RuntimeError('collection_cancelled')
                        with self.database(publication_guard=publication_guard, cancelled=cancelled) as confirmation:
                            confirmation.execute('UPDATE target_snapshots SET source_order=? WHERE target_id=?',
                                                 (effective_order, target_id))
                            confirmation.execute('UPDATE targets SET next_run=?+interval_seconds WHERE id=?', (time.time(), target_id))
                            if observation is not None:
                                saved=json.loads(target['cursor'] or '{}')
                                if not isinstance(saved,dict):raise RuntimeError('collection_cursor_integrity')
                                old_observation=source_observation(saved,source_revision)
                                saved['source_observation']=old_observation if old_observation and old_observation['observed_at']>observation['observed_at'] else observation
                                confirmation.execute('UPDATE targets SET cursor=?,last_success=? WHERE id=?',
                                                     (encoded(saved).decode(),time.time(),target_id))
                            observation_id=record_source_observation(confirmation,target_id,source_revision,processing_version,
                                projection,observation,prior['run_id'],len(records),len(relations))
                        return {'run_id': prior['run_id'], 'target_id': target_id, 'state': 'unchanged',
                                'added': 0, 'revised': 0, 'unchanged': len(records), 'removed': 0,
                                **({'source_observation_id':observation_id} if observation_id else {})}
                    if automatic_snapshot_run and prior:
                        # A -> B -> A is a new transition, not a replay of A's
                        # completed run. The locked predecessor also keeps a
                        # failed/cancelled transition's retry identity stable.
                        run_id = identity('snapshot-transition', encoded([run_id, prior['run_id']]).decode())
                previous = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
                if previous:
                    if previous["target_id"] != target_id or previous["origin"] != origin:
                        raise RuntimeError("collection_run_identity_conflict")
                    if previous["state"] == "complete":
                        return dict(previous)
                db.execute("INSERT OR REPLACE INTO runs(id,target_id,origin,state,started_at,cursor_before) VALUES(?,?,?,?,?,?)",
                           (run_id, target_id, origin, "running", time.time(), target["cursor"]))
            try:
                with self.database(publication_guard=publication_guard, cancelled=cancelled) as db:
                    db.execute('BEGIN IMMEDIATE')
                    # document_id is UNINDEXED in FTS5. Deleting once per input
                    # record scans the entire corpus each time. Stage each
                    # final body, then update the projection in one table scan.
                    db.execute('CREATE TEMP TABLE search_updates(document_id TEXT PRIMARY KEY,label TEXT,body TEXT)')
                    if complete_snapshot:
                        prior_relations = dict(db.execute('SELECT id,version FROM relations WHERE target_id=? AND active=1', (target_id,)))
                        db.execute('CREATE TEMP TABLE snapshot_members(document_id TEXT PRIMARY KEY)')
                        db.execute('CREATE TEMP TABLE snapshot_relations(id TEXT PRIMARY KEY)')
                    for index, record in enumerate(records):
                        if index >= (SNAPSHOT_RECORD_BUDGET if complete_snapshot else MAX_BATCH_RECORDS):
                            raise ValueError("collection_batch_budget")
                        if cancelled():
                            raise RuntimeError("collection_cancelled")
                        platform = record.get("platform", target["platform"])
                        original_id = record["original_id"]
                        doc_id = identity(platform, original_id)
                        raw = encoded(record.get("raw", record))
                        raw_sha, raw_path = self._blob(raw)
                        label = normalized_text(str(record.get("label", original_id)))[:1024]
                        body = normalized_body(str(record.get("text", "")))
                        metadata = {k: v for k, v in record.items() if k not in {"raw", "text"}}
                        projection_sha = digest(encoded([label, body, {k:v for k,v in metadata.items() if k != 'source'}]))
                        version = identity("version", encoded([doc_id, raw_sha, processing_version, projection_sha]).decode())
                        old = db.execute("SELECT current_version FROM documents WHERE id=?", (doc_id,)).fetchone()
                        membership = db.execute("SELECT current_version,availability FROM memberships WHERE target_id=? AND document_id=?", (target_id, doc_id)).fetchone()
                        prior_version = membership[0] if membership else old[0] if old else None
                        category = "added" if old is None else "unchanged" if prior_version == version else "revised"
                        if membership and membership['availability'] != 'available':
                            category = 'revised'
                        counts[category] += 1
                        self._event(db, run_id, target_id, "discovered", document_id=doc_id, version=version)
                        db.execute("INSERT INTO documents VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET current_version=excluded.current_version,availability=excluded.availability",
                                   (doc_id, platform, original_id, version, "available"))
                        inserted = db.execute("INSERT OR IGNORE INTO versions VALUES(?,?,?,?,?,?,?,?,?,?)",
                                   (version, doc_id, raw_sha, raw_path, label, body, encoded(metadata).decode(), time.time(), processing_version, projection_sha))
                        if inserted.rowcount:
                            db.execute("INSERT INTO version_search VALUES(?,?,?,?)", (version, doc_id, label, body))
                        self._event(db, run_id, target_id, "parsed", document_id=doc_id, version=version)
                        self._event(db, run_id, target_id, "validated", document_id=doc_id, version=version,
                                    validation="identity, byte hash and shape only; not semantic truth")
                        db.execute("""INSERT INTO memberships(target_id,document_id,current_version,availability) VALUES(?,?,?,'available')
                          ON CONFLICT(target_id,document_id) DO UPDATE SET current_version=excluded.current_version,availability='available'""",
                                   (target_id, doc_id, version))
                        if complete_snapshot:
                            db.execute('INSERT INTO snapshot_members VALUES(?)', (doc_id,))
                        self._event(db, run_id, target_id, "stored", document_id=doc_id, version=version, change=category, **activity_details)
                        db.execute('INSERT INTO search_updates VALUES(?,?,?) ON CONFLICT(document_id) DO UPDATE SET label=excluded.label,body=excluded.body', (doc_id,label,body))
                        self._event(db, run_id, target_id, "indexed", document_id=doc_id, version=version,
                                    index="sqlite_fts5", dense_index="not_yet_confirmed")
                    if cancelled():raise RuntimeError('collection_cancelled')
                    db.execute('DELETE FROM document_search WHERE document_id IN (SELECT document_id FROM search_updates)')
                    db.execute('INSERT INTO document_search SELECT document_id,label,body FROM search_updates')
                    for relation in relations:
                        source = identity(relation["source_platform"], relation["source_id"])
                        destination = identity(relation["target_platform"], relation["target_id"])
                        if not all(db.execute("SELECT 1 FROM documents WHERE id=?", (value,)).fetchone() for value in (source, destination)):
                            raise ValueError("collection_orphan_relation")
                        rel_id = identity("relation", encoded([target_id, source, relation["type"], destination, relation.get("original_id", "")]).decode())
                        evidence = encoded(relation.get("evidence", {})).decode()
                        reused_evidence=False
                        if complete_snapshot and prior and prior['source_revision'] == source_revision:
                            old_relation=db.execute('SELECT id,version,target_id,source,target,type,evidence,active FROM relations WHERE id=?',(rel_id,)).fetchone()
                            if old_relation is not None:
                                require_relation_binding(db,old_relation)
                                if tuple(old_relation[k] for k in ('target_id','source','target','type')) != (target_id,source,destination,relation['type']):
                                    raise RuntimeError('collection_relation_integrity')
                                if same_file_relation_evidence(old_relation['evidence'],relation.get('evidence',{}),source_revision):
                                    evidence=old_relation['evidence']
                                    reused_evidence=True
                                    if not old_relation['active']:db.execute('UPDATE relations SET active=1 WHERE id=?',(rel_id,))
                        if not reused_evidence:
                            require_relation_binding(db, {'id':rel_id,'version':digest(evidence.encode()),
                                'target_id':target_id,'source':source,'target':destination,
                                'type':relation['type'],'evidence':evidence}, allow_new=True)
                            db.execute("INSERT INTO relations VALUES(?,?,?,?,?,?,?,1) ON CONFLICT(id) DO UPDATE SET evidence=excluded.evidence,version=excluded.version,active=1",
                                       (rel_id, target_id, source, destination, relation["type"], evidence, digest(evidence.encode())))
                            db.execute('INSERT OR IGNORE INTO relation_versions VALUES(?,?,?,?,?,?,?,?)',
                                       (rel_id, digest(evidence.encode()), target_id, source, destination, relation['type'], evidence, time.time()))
                        if complete_snapshot:
                            if not all(db.execute('SELECT 1 FROM snapshot_members WHERE document_id=?', (value,)).fetchone() for value in (source, destination)):
                                raise ValueError('collection_orphan_relation')
                            db.execute('INSERT OR IGNORE INTO snapshot_relations VALUES(?)', (rel_id,))
                    removed = 0
                    for document_id in retracted_ids:
                        if cancelled():raise RuntimeError('collection_cancelled')
                        row = db.execute("SELECT current_version FROM memberships WHERE target_id=? AND document_id=? AND availability='available'", (target_id, document_id)).fetchone()
                        if row is None:continue
                        db.execute("UPDATE memberships SET availability='deleted' WHERE target_id=? AND document_id=?", (target_id, document_id))
                        db.execute("UPDATE documents SET availability='deleted' WHERE id=? AND NOT EXISTS(SELECT 1 FROM memberships WHERE document_id=? AND availability='available')", (document_id, document_id))
                        self._event(db, run_id, target_id, 'stored', document_id=document_id, version=row['current_version'], change='removed', reason='현재 원본 리더가 본문을 제공하지 않습니다. 이전 버전은 보존했습니다.', **activity_details)
                        removed += 1
                    if complete_snapshot:
                        missing = db.execute("SELECT document_id,current_version FROM memberships WHERE target_id=? AND availability='available' AND document_id NOT IN (SELECT document_id FROM snapshot_members)", (target_id,)).fetchall()
                        removed = len(missing)
                        for row in missing:
                            db.execute("UPDATE memberships SET availability='deleted' WHERE target_id=? AND document_id=?", (target_id, row['document_id']))
                            db.execute("UPDATE documents SET availability='deleted' WHERE id=? AND NOT EXISTS(SELECT 1 FROM memberships WHERE document_id=? AND availability='available')", (row['document_id'], row['document_id']))
                            self._event(db, run_id, target_id, 'stored', document_id=row['document_id'], version=row['current_version'], change='removed', reason='완전한 원본 스냅샷에서 제외되었습니다. 원문과 이전 버전은 보존했습니다.', **activity_details)
                        db.execute('UPDATE relations SET active=0 WHERE target_id=? AND id NOT IN (SELECT id FROM snapshot_relations)', (target_id,))
                        current_relations = dict(db.execute('SELECT id,version FROM relations WHERE target_id=? AND active=1', (target_id,)))
                        if prior_relations != current_relations:
                            self._event(db, run_id, target_id, 'stored', change='relations_changed',
                                        added=len(current_relations.keys()-prior_relations.keys()),
                                        removed=len(prior_relations.keys()-current_relations.keys()),
                                        reason='저장된 관계와 근거가 갱신되었습니다.', **activity_details)
                        db.execute('INSERT OR REPLACE INTO target_snapshots VALUES(?,?,?,?,?,?)',
                                   (target_id, source_revision, effective_order, processing_version, projection, run_id))
                    if cancelled():
                        raise RuntimeError("collection_cancelled")
                    now = time.time()
                    db.execute("UPDATE runs SET state='complete',ended_at=?,cursor_after=?,added=?,revised=?,unchanged=? WHERE id=?",
                               (now, encoded(cursor).decode(), counts["added"], counts["revised"], counts["unchanged"], run_id))
                    db.execute("UPDATE targets SET cursor=?,last_success=?,last_error=NULL,next_run=?+interval_seconds WHERE id=?",
                               (encoded(cursor).decode(), now, now, target_id))
                    observation_id=record_source_observation(db,target_id,source_revision,processing_version,projection,
                        observation if complete_snapshot else None,run_id,len(records) if complete_snapshot else 0,len(relations) if complete_snapshot else 0)
                return {"run_id": run_id, "target_id": target_id, "state": "complete", **counts, "removed": removed,
                        **({'source_observation_id':observation_id} if observation_id else {})}
            except Exception as error:
                reason = str(error)[:200]
                state = "paused" if reason == "collection_cancelled" or isinstance(error, AldenCancelled) else "failed"
                with self.database() as db:
                    db.execute("UPDATE runs SET state=?,ended_at=?,error=? WHERE id=?", (state, time.time(), reason, run_id))
                    db.execute("UPDATE targets SET last_error=? WHERE id=?", (reason, target_id))
                    self._event(db, run_id, target_id, state, reason=reason)
                raise

    def release_activity(self, target_id, run_id, *, publication_guard, cancelled=lambda: False):
        """Append idempotent readiness receipts after the source owner commits.

        This is a durable refresh signal, not UI acknowledgement or a semantic
        verification. The owner must hold its publication fence across commit.
        """
        if not callable(publication_guard) or not isinstance(run_id, str) or not run_id:
            raise ValueError('collection_activity_release_invalid')
        with self.target_lock(target_id), self.database(publication_guard=publication_guard, cancelled=cancelled) as db:
            db.execute('BEGIN IMMEDIATE')
            run = db.execute("SELECT 1 FROM runs WHERE id=? AND target_id=? AND state='complete'", (run_id, target_id)).fetchone()
            if run is None:
                raise ValueError('collection_activity_run_incomplete')
            rows = db.execute("""SELECT event_id,document_id,version,details FROM events
                WHERE run_id=? AND target_id=? AND stage='stored'
                  AND json_extract(details,'$.activity_deferred')=1 ORDER BY sequence""", (run_id, target_id)).fetchall()
            if len(rows) > MAX_BATCH_RECORDS:
                raise ValueError('collection_activity_release_budget')
            eligible = 0
            for row in rows:
                if cancelled():
                    raise RuntimeError('collection_cancelled')
                details = json.loads(row['details'])
                if details.get('change') not in {'added', 'revised', 'removed', 'relations_changed'}:
                    continue
                self._event(db, run_id, target_id, 'published', document_id=row['document_id'],
                            version=row['version'], change=details['change'], stored_event_id=row['event_id'])
                eligible += 1
            return {'eligible_receipts': eligible}

    def events(self, *, after=0, limit=100, projects=None, target_id=None, stage=None) -> dict:
        if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("collection_event_cursor_invalid")
        if projects is None:
            projects = [p["project"] for p in self.projects()]
        clauses = ["e.sequence>?", "alden_source_allowed(e.target_id,e.document_id,e.version)=1"];args = [after]
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
            for row in db.execute("SELECT r.* FROM relations r WHERE active=1 AND " + self._source_relation_scope() + " AND EXISTS(SELECT 1 FROM target_projects p WHERE p.target_id=r.target_id AND p.project IN (" + marks + ") AND p.permission!='denied')", projects):
                if row["source"] in allowed and row["target"] in allowed:
                    require_relation_binding(db, row)
                    edges.append({"id": row["id"], "source": row["source"], "target": row["target"],
                                  "relation": row["type"], "weight": 1, "context": "explicit source relationship",
                                  "purpose": "reference", "evidence_message_id": row["id"],
                                  "evidence": {"kind": "snapshot", "source_event_ids": [row["id"]], "chat_id": "", "confirmed_at": None, "retracted": False},
                                  "source_evidence": json.loads(row["evidence"])})
            return {"nodes": nodes, "edges": edges, "scope": projects, "canonical": "independent original sources; this is a derived projection"}

    def search(self, query: str, *, projects: list[str], limit=10, time_from=None, time_to=None, expand_korean=True) -> list[dict]:
        if type(expand_korean) is not bool:
            raise ValueError('collection_graph_filter_invalid')
        if not projects:
            return []
        with self.database() as db:
            clauses, values = [], []
            for value, operator in [(time_from, '>='), (time_to, '<=')]:
                if value is not None:
                    clauses.append('v.collected_at' + operator + '?');values.append(value)
            plan = korean_literal_plan(query) if expand_korean else None
            if plan:
                contains, params = korean_literal_sql(plan)
                db.create_function('alden_korean_literal',3,
                    lambda label,body,word:int(korean_literal_matches(((word,),),label,body)),deterministic=True)
                strict, strict_params = korean_literal_sql(plan,'s.label','s.body',numeric_boundaries=True)
                sql = """WITH search_candidates AS MATERIALIZED (
                    SELECT document_id FROM version_search WHERE version_search MATCH ?
                    UNION SELECT v.document_id FROM versions v WHERE """ + contains + """),
                    scoped AS MATERIALIZED (
                    SELECT d.id,s.current_version,v.label,v.body,v.metadata
                    FROM (""" + self._scope_versions(projects, candidates=True) + """) s
                    JOIN documents d ON d.id=s.document_id JOIN versions v ON v.id=s.current_version
                    WHERE d.availability='available' """ + ''.join(' AND ' + c for c in clauses) + """),
                    token_matches AS MATERIALIZED (
                    SELECT s.id,bm25(version_search) AS rank FROM version_search
                    JOIN scoped s ON s.current_version=version_search.version_id WHERE version_search MATCH ?),
                    matches AS (SELECT id,rank,0 AS priority FROM token_matches UNION ALL
                    SELECT s.id,0.0,1 FROM scoped s WHERE """ + strict + """
                    AND s.id NOT IN (SELECT id FROM token_matches))
                    SELECT s.id,s.label,s.body,s.metadata,m.rank FROM matches m JOIN scoped s ON s.id=m.id
                    ORDER BY m.priority,m.rank,m.id LIMIT ?"""
                rows = db.execute(sql, (query, *params, *projects, *values, query, *strict_params, limit)).fetchall()
            else:
                rows = db.execute("""WITH search_candidates AS MATERIALIZED (
              SELECT document_id FROM version_search WHERE version_search MATCH ?)
              SELECT d.id,v.label,v.body,v.metadata,bm25(version_search) AS rank
              FROM version_search JOIN documents d ON d.id=version_search.document_id
              JOIN (""" + self._scope_versions(projects, candidates=True) + """) s ON s.document_id=d.id
              AND s.current_version=version_search.version_id JOIN versions v ON v.id=s.current_version
              WHERE version_search MATCH ? AND d.availability='available' """ + ''.join(' AND ' + c for c in clauses) + ' ORDER BY rank,d.id LIMIT ?',
              (query, *projects, query, *values, limit)).fetchall()
            results = []
            for row in rows:
                metadata = json.loads(row['metadata'])
                source = metadata.get('source')
                if isinstance(source, dict) and source.get('kind') == 'messenger-exact-content-provenance':
                    # A shared version retains its first publisher privately.
                    # Its old account/consent receipt is not search provenance.
                    metadata['source'] = {'kind': source['kind'], 'content_hash': source.get('content_hash'),
                        'scope': 'content_identity_only; consult current scoped provenance for source memberships'}
                results.append({**dict(row), 'metadata': metadata})
            return results

    def _scope_versions(self, projects: list[str], *, candidates=False) -> str:
        marks = ",".join("?" for _ in projects)
        available = "m.availability='available' AND " if self.schema >= 3 else ""
        candidate_scope = "m.document_id IN (SELECT document_id FROM search_candidates) AND " if candidates else ""
        return """SELECT document_id,current_version FROM (
          SELECT m.document_id,m.current_version,ROW_NUMBER() OVER(
            PARTITION BY m.document_id ORDER BY v.collected_at DESC,m.target_id) AS choice
          FROM memberships m JOIN versions v ON v.id=m.current_version
          WHERE """ + available + candidate_scope + """alden_source_allowed(m.target_id,m.document_id,m.current_version)=1
            AND EXISTS(SELECT 1 FROM target_projects p WHERE p.target_id=m.target_id
            AND p.permission!='denied' AND p.project IN (""" + marks + "))) WHERE choice=1"

    def _source_relation_scope(self):
        guards = []
        for endpoint in ('source', 'target'):
            allowed = 'alden_source_allowed(r.target_id,r.' + endpoint + ',m.current_version)'
            available = 'm.current_version IS NOT NULL' + (" AND m.availability='available'" if self.schema >= 3 else '')
            allowed = 'CASE WHEN ' + available + ' THEN ' + allowed + ' ELSE 0 END'
            # A retained withdrawal must not fall back to another source's
            # visible copy. A never-owned endpoint can still be an explicit
            # external reference; callers independently scope its visible node.
            guards.append('COALESCE((SELECT ' + allowed + ' FROM memberships m'
                ' WHERE m.target_id=r.target_id AND m.document_id=r.' + endpoint + '),'
                'alden_source_allowed(r.target_id,r.' + endpoint + ',NULL))=1')
        return ' AND '.join(guards)

    def _source_authority_snapshot(self, db, projects):
        if not projects:
            return ()
        marks = ','.join('?' for _ in projects)
        rows = db.execute('''SELECT target_id,project,permission,
            CASE WHEN permission!='denied' THEN alden_source_scope(target_id) END
            FROM target_projects WHERE project IN (''' + marks + ''')
            ORDER BY target_id,project LIMIT ?''', (*projects, SNAPSHOT_RECORD_BUDGET + 1)).fetchall()
        if len(rows) > SNAPSHOT_RECORD_BUDGET:
            raise RuntimeError('collection_retrieval_budget')
        return tuple(tuple(row) for row in rows)

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

    def graph_references(self, **options) -> dict:
        """Internal affinity selection; the ordinary graph view stays complete."""
        return self.graph_page(**options, _references_only=_GRAPH_REFERENCES)

    def graph_page(self, *, projects: list[str], limit=120, offset=0, focus=None, hops=1, query="",
                   target_id=None, platform=None, node_type=None, relation=None, since=None, until=None,
                   details=False, expected_version=None, overview=False, _references_only=False) -> dict:
        if type(limit) is not int or not 1 <= limit <= (1984 if overview is True and not focus else 120) or type(offset) is not int or offset < 0 or type(hops) is not int or not 0 <= hops <= 3:
            raise ValueError("collection_graph_budget_invalid")
        if type(overview) is not bool:
            raise ValueError("collection_graph_budget_invalid")
        if (_references_only is not False and _references_only is not _GRAPH_REFERENCES
                or _references_only is _GRAPH_REFERENCES and details):
            raise ValueError("collection_graph_budget_invalid")
        for value in (focus, target_id, node_type, relation, expected_version):
            if value is not None and (not isinstance(value, str) or not value or len(value) > 256):
                raise ValueError("collection_graph_filter_invalid")
        if platform is not None and platform not in PLATFORMS:
            raise ValueError("collection_platform_invalid")
        for value in (since, until):
            if value is not None and (type(value) not in {int, float} or not math.isfinite(value) or value < 0):
                raise ValueError("collection_event_time_invalid")
        if since is not None and until is not None and since >= until:
            raise ValueError("collection_event_time_invalid")
        if type(details) is not bool or not isinstance(query, str) or len(query) > 256:
            raise ValueError("collection_graph_filter_invalid")
        # Unfiltered overview drawing omits file-tree edges. The stored
        # relations and non-overview pages keep them.
        draw_topical_edges = overview is True and focus is None and query == "" and relation is None
        with self.database() as db:
            db.create_function('alden_relation_digest', 1, relation_evidence_digest, deterministic=True)
            from alden_content_evidence import content_evidence_class
            db.create_function('alden_content_class',2,lambda meta,body:content_evidence_class(json.loads(meta),body),deterministic=True)
            db.create_function('alden_overview_tier', 1, overview_label_tier, deterministic=True)
            db.create_function('alden_sample_tier', 2, overview_sample_tier, deterministic=True)
            db.create_function('alden_sample_rank', 3, overview_sample_rank, deterministic=True)
            db.create_function('alden_display_label', 2, overview_display_label, deterministic=True)
            checkpoint, _, _, _ = self._activity_scope(db, projects, target_id, platform)
            if not projects:
                if _references_only:
                    return {"ok": True, "nodes": [], "total_nodes": 0, "total_edges": 0,
                            "next": None, "revision": 0, "projection": "current_graph_references"}
                return {"ok": True, "nodes": [], "edges": [], "total_nodes": 0, "total_edges": 0,
                        "next": None, "activity_checkpoint": checkpoint}
            marks = ",".join("?" for _ in projects)
            # Materialize the permitted versions once per SQL statement. Target
            # selection precedes version ranking, so another target's revision
            # cannot replace the selected target's record.
            available = "m.availability='available' AND " if self.schema >= 3 else ""
            cte = """WITH ranked AS MATERIALIZED (
              SELECT m.document_id,m.current_version,m.target_id,ROW_NUMBER() OVER(
                PARTITION BY m.document_id ORDER BY v.collected_at DESC,m.target_id) AS choice
              FROM memberships m JOIN versions v ON v.id=m.current_version JOIN targets t ON t.id=m.target_id
              WHERE """ + available + """alden_source_allowed(m.target_id,m.document_id,m.current_version)=1
                AND EXISTS(SELECT 1 FROM target_projects p WHERE p.target_id=m.target_id
                AND p.permission!='denied' AND p.project IN (""" + marks + """))
                AND (? IS NULL OR m.target_id=?) AND (? IS NULL OR t.platform=?)),
              visible AS MATERIALIZED (
              SELECT d.id,d.platform,d.original_id,s.current_version AS visible_version,s.target_id,v.collected_at
              FROM ranked s JOIN documents d ON d.id=s.document_id JOIN versions v ON v.id=s.current_version
              WHERE s.choice=1 AND d.availability='available' AND (? IS NULL OR json_extract(v.metadata,'$.kind')=?)
                AND (? IS NULL OR v.collected_at>=?) AND (? IS NULL OR v.collected_at<?)),
              permitted_relationships AS MATERIALIZED (
              SELECT r.*, (""" + RELATION_HISTORY_MATCH + """
                AND r.version IS alden_relation_digest(r.evidence)) AS history_bound
              FROM relations r JOIN visible a ON a.id=r.source JOIN visible b ON b.id=r.target
              LEFT JOIN relation_versions h ON h.relation_id=r.id AND h.version=r.version
              WHERE r.active=1 AND """ + self._source_relation_scope() + """ AND (? IS NULL OR r.target_id=?)
                AND EXISTS(SELECT 1 FROM target_projects p WHERE p.target_id=r.target_id
                  AND p.permission!='denied' AND p.project IN (""" + marks + """))),
              permitted_edges AS MATERIALIZED (SELECT * FROM permitted_relationships WHERE ? IS NULL OR type=?),
              shown AS MATERIALIZED (SELECT * FROM visible WHERE ? IS NULL OR id IN (
                SELECT source FROM permitted_edges UNION SELECT target FROM permitted_edges)),
              degrees AS (SELECT id,COUNT(DISTINCT neighbor) AS degree FROM (
                SELECT source AS id,target AS neighbor FROM permitted_edges WHERE source!=target
                UNION ALL SELECT target AS id,source AS neighbor FROM permitted_edges WHERE source!=target) GROUP BY id) """
            visible_args = [*projects, target_id, target_id, platform, platform, node_type, node_type,
                            since, since, until, until]
            relation_args = [target_id, target_id, *projects, relation, relation, relation]
            args = [*visible_args, *relation_args]

            def incident_scope(current_ids):
                slots = ",".join("?" for _ in current_ids)
                sql = cte.replace("WHERE r.active=1", "WHERE r.active=1 AND (r.source IN ("
                                  + slots + ") OR r.target IN (" + slots + "))", 1)
                return sql, (*visible_args, *current_ids, *current_ids, *relation_args)
            fields = """s.id,s.platform,s.original_id,s.visible_version,s.target_id,s.collected_at,
              v.label,v.body,v.metadata,v.raw_sha256,v.raw_path,COALESCE(json_extract(v.metadata,'$.source_url'),CASE WHEN json_type(v.metadata,'$.url')='text' THEN json_extract(v.metadata,'$.url') END) AS source_url"""
            if details and focus and hops == 0 and relation is None:
                # An already-selected record needs one exact permitted lookup,
                # not repeated materialization of all 26k nodes and 48k edges.
                exact = cte.replace("WHERE " + available + "alden_source_allowed(",
                                    "WHERE m.document_id=? AND " + available + "alden_source_allowed(", 1)
                row = db.execute(exact + "SELECT " + fields + " FROM shown s JOIN versions v ON v.id=s.visible_version WHERE s.id=?", (focus, *args, focus)).fetchone()
                if row is None:
                    raise ValueError("collection_graph_focus_not_in_scope")
                return self._record_details(db, row, expected_version)
            json_node = """json_object('id',s.id,'platform',s.platform,'original_id',s.original_id,
              'visible_version',s.visible_version,'target_id',s.target_id,'collected_at',s.collected_at,
              'label',alden_display_label(v.label,v.body),'body',substr(v.body,1,2400),'metadata',json_object('kind',substr(CAST(json_extract(v.metadata,'$.kind') AS TEXT),1,256)),
              'content_evidence',alden_content_class(v.metadata,v.body),
              'source_url',COALESCE(json_extract(v.metadata,'$.source_url'),CASE WHEN json_type(v.metadata,'$.url')='text' THEN json_extract(v.metadata,'$.url') END),'degree',COALESCE(d.degree,0))"""
            if overview:
                json_node = json_node.replace("substr(v.body,1,2400)", "substr(v.body,1,0)")
            if not focus:
                # All overview outputs share one materialized permitted scope.
                # LIMIT+1 remains inside SQL, including FTS pages.
                plan = korean_literal_plan(query)
                fts = (' OR '.join(' AND '.join('"' + word + '"' for word in clause) for clause in plan)
                       if plan else ' AND '.join('"' + term.replace('"', '""') + '"' for term in query.split()))
                picking = ("SELECT s.id FROM version_search JOIN shown s ON s.visible_version=version_search.version_id WHERE version_search MATCH ? ORDER BY bm25(version_search),s.id" if fts else
                           "SELECT s.id FROM shown s LEFT JOIN degrees d ON d.id=s.id ORDER BY COALESCE(d.degree,0) DESC,s.id")
                if overview and not fts:
                    # Interleave real source targets so a large project's hubs
                    # cannot occupy the whole unified overview. No fake links.
                    picking = """SELECT id FROM (SELECT s.id,
                      ROW_NUMBER() OVER(PARTITION BY s.target_id
                        ORDER BY alden_sample_rank(v.label, v.body, json_extract(v.metadata,'$.kind')), COALESCE(d.degree,0) DESC, s.id) AS source_slot,
                      ROW_NUMBER() OVER(PARTITION BY CASE WHEN length(trim(alden_display_label(v.label,v.body)))<2 THEN s.id
                        ELSE alden_display_label(v.label,v.body) END
                        ORDER BY COALESCE(d.degree,0) DESC, s.id) AS label_copy
                      FROM shown s LEFT JOIN degrees d ON d.id=s.id JOIN versions v ON v.id=s.visible_version
                      WHERE alden_sample_rank(v.label, v.body, json_extract(v.metadata,'$.kind')) < 3)
                      WHERE label_copy=1 ORDER BY source_slot,id"""
                search_cte = cte
                if plan:
                    contains, params = korean_literal_sql(plan)
                    db.create_function('alden_korean_literal',3,
                        lambda label,body,word:int(korean_literal_matches(((word,),),label,body)),deterministic=True)
                    strict, strict_params = korean_literal_sql(plan,numeric_boundaries=True)
                    search_cte += """,token_matches AS MATERIALIZED (
                      SELECT s.id,bm25(version_search) AS rank FROM version_search JOIN shown s
                      ON s.visible_version=version_search.version_id WHERE version_search MATCH ?),
                      literal_versions AS MATERIALIZED (SELECT v.id,v.label,v.body FROM versions v WHERE """ + contains + ")"
                    picking = """SELECT id FROM (SELECT id,rank,0 AS priority FROM token_matches UNION ALL
                      SELECT s.id,0.0,1 FROM shown s JOIN literal_versions v ON v.id=s.visible_version
                      WHERE """ + strict + """ AND s.id NOT IN (SELECT id FROM token_matches)) ORDER BY priority,rank,id"""
                    pick_args = [fts, *params, *strict_params, limit+1, offset, limit]
                else:
                    pick_args = [fts, limit+1, offset, limit] if fts else [limit+1, offset, limit]
                overview = search_cte + ",picked AS MATERIALIZED (" + picking + " LIMIT ? OFFSET ?),selected AS MATERIALIZED (SELECT id FROM picked LIMIT ?) "
                nodes_json, edges_json, total, total_edges, more, kinds, types, invalid_relations = db.execute(overview + """SELECT
                  (SELECT json_group_array(json(item)) FROM (SELECT """ + json_node + """ AS item
                    FROM selected p JOIN shown s ON s.id=p.id JOIN versions v ON v.id=s.visible_version
                    LEFT JOIN degrees d ON d.id=s.id ORDER BY s.id)),
                  (SELECT json_group_array(json(item)) FROM (SELECT json_object('id',r.id,'source',r.source,'target',r.target,'type',r.type) AS item
                    FROM permitted_edges r JOIN selected a ON a.id=r.source JOIN selected b ON b.id=r.target ORDER BY r.id LIMIT """ + ("4096" if overview else "512") + """)),
                  (SELECT COUNT(*) FROM shown),(SELECT COUNT(*) FROM permitted_edges),(SELECT COUNT(*) FROM picked)>?,
                  (SELECT json_group_array(kind) FROM (SELECT DISTINCT json_extract(v.metadata,'$.kind') AS kind FROM ranked s
                    JOIN versions v ON v.id=s.current_version JOIN documents d ON d.id=s.document_id
                    WHERE s.choice=1 AND d.availability='available' AND kind IS NOT NULL ORDER BY kind LIMIT 128)),
                  (SELECT json_group_array(type) FROM (SELECT DISTINCT type FROM permitted_relationships ORDER BY type LIMIT 128)),
                  (SELECT COUNT(*) FROM permitted_relationships WHERE history_bound!=1)""", (*args, *pick_args, limit)).fetchone()
                if invalid_relations: raise RuntimeError('collection_relation_integrity')
                rows = json.loads(nodes_json)
                # JSON1 already validates and constructs the same edge JSON.
                # An affinity caller needs its selected refs, not thousands of
                # additional Python edge dictionaries or their display payload.
                edge_rows = [] if _references_only else json.loads(edges_json)
                del nodes_json, edges_json
                ids = [r["id"] for r in rows]
            else:
                total, total_edges, kinds, types, first_neighbors, focus_exists, invalid_relations = db.execute(cte + """, focus_neighbors AS (
              SELECT target AS neighbor FROM permitted_edges WHERE source=?
              UNION SELECT source AS neighbor FROM permitted_edges WHERE target=?)
              SELECT (SELECT COUNT(*) FROM shown),(SELECT COUNT(*) FROM permitted_edges),
              (SELECT json_group_array(kind) FROM (SELECT DISTINCT json_extract(v.metadata,'$.kind') AS kind
                FROM ranked s JOIN versions v ON v.id=s.current_version JOIN documents d ON d.id=s.document_id
                WHERE s.choice=1 AND d.availability='available' AND kind IS NOT NULL ORDER BY kind LIMIT 128)),
              (SELECT json_group_array(type) FROM (SELECT DISTINCT type FROM permitted_relationships ORDER BY type LIMIT 128)),
              (SELECT json_group_array(neighbor) FROM (SELECT neighbor FROM focus_neighbors WHERE neighbor!=?
                ORDER BY neighbor COLLATE BINARY LIMIT ?)),
              EXISTS(SELECT 1 FROM shown WHERE id=?),
              (SELECT COUNT(*) FROM permitted_relationships WHERE history_bound!=1)""", (*args, focus, focus, focus,
                  min(limit, 24)-1 if hops else 0, focus)).fetchone()
                if invalid_relations: raise RuntimeError('collection_relation_integrity')
                if not focus_exists:
                    raise ValueError("collection_graph_focus_not_in_scope")
                # Keep traversal inside the same permitted SQL snapshot. Only
                # the next bounded ID set crosses into Python; degree remains
                # the unique non-self neighbors in the full filtered scope.
            facets = {"node_types": json.loads(kinds), "relations": json.loads(types)}
            if focus:
                ids = [focus, *json.loads(first_neighbors)]
                for _ in range(max(0, hops-1)):
                    remaining = min(limit, 24) - len(ids)
                    if remaining <= 0:
                        break
                    seen = ",".join("?" for _ in ids)
                    incident_cte, incident_args = incident_scope(ids)
                    neighbors = [row[0] for row in db.execute(incident_cte + """, neighbors AS (
                      SELECT target AS neighbor FROM permitted_edges WHERE source IN (""" + seen + ") "
                      "UNION SELECT source AS neighbor FROM permitted_edges WHERE target IN (" + seen + ")) "
                      "SELECT neighbor FROM neighbors WHERE neighbor NOT IN (" + seen + ") "
                      "ORDER BY neighbor COLLATE BINARY LIMIT ?", (*incident_args, *ids, *ids, *ids, remaining))]
                    ids.extend(neighbors)
                    if not neighbors or len(ids) >= min(limit, 24):
                        break
                more = False
            if not ids:
                if _references_only:
                    return {"ok": True, "nodes": [], "total_nodes": total, "total_edges": total_edges,
                            "next": None, "revision": 0, "projection": "current_graph_references"}
                return {"ok": True, "nodes": [], "edges": [], "total_nodes": total, "total_edges": total_edges,
                        "next": None, "facets": facets, "activity_checkpoint": checkpoint}
            selected = ",".join("?" for _ in ids)
            if focus:
                incident_cte, incident_args = incident_scope(ids)
                focus_node = """json_object('id',s.id,'platform',s.platform,'original_id',s.original_id,
                  'visible_version',s.visible_version,'target_id',s.target_id,'collected_at',s.collected_at,
                  'label',alden_display_label(v.label,v.body),'body',v.body,'metadata',v.metadata,'raw_sha256',v.raw_sha256,
                  'raw_path',v.raw_path,'source_url',COALESCE(json_extract(v.metadata,'$.source_url'),CASE WHEN json_type(v.metadata,'$.url')='text' THEN json_extract(v.metadata,'$.url') END),
                  'degree',COALESCE(d.degree,0))"""
                nodes_json, edges_json = db.execute(incident_cte + "SELECT "
                    + "(SELECT json_group_array(json(node)) FROM (SELECT " + focus_node
                    + " AS node FROM visible s JOIN versions v ON v.id=s.visible_version "
                    + "LEFT JOIN degrees d ON d.id=s.id WHERE s.id IN (" + selected + ") ORDER BY s.id)),"
                    + "(SELECT json_group_array(json_object('id',id,'source',source,'target',target,'type',type)) "
                    + "FROM (SELECT id,source,target,type FROM permitted_edges WHERE source IN (" + selected
                    + ") AND target IN (" + selected + ") ORDER BY id COLLATE BINARY LIMIT 144))",
                    (*incident_args, *ids, *ids, *ids)).fetchone()
                rows = json.loads(nodes_json)
                edge_rows = [] if _references_only else json.loads(edges_json)
                del nodes_json, edges_json
                # JSON1 formats REAL values with fewer significant digits than
                # SQLite's native float read. Keep the source clock exact using
                # one bounded primary-key lookup in this same read snapshot.
                versions = [row["visible_version"] for row in rows]
                clock_slots = ",".join("?" for _ in versions)
                clocks = {row["id"]: row["collected_at"] for row in db.execute(
                    "SELECT id,collected_at FROM versions WHERE id IN (" + clock_slots + ")", versions)}
                for row in rows:
                    row["collected_at"] = clocks[row["visible_version"]]
            target_spaces = {}
            for association in db.execute("SELECT target_id,project FROM target_projects WHERE permission!='denied' AND project IN (" + marks + ") ORDER BY project", projects):
                target_spaces.setdefault(association["target_id"], []).append(association["project"])
            source_contexts = {}
            # Preserve every permitted current membership behind a shared ID.
            # A union's chosen latest version does not replace another project's
            # current source context or authorize a decision for that context.
            contexts_sql = """SELECT m.document_id,m.target_id,m.current_version,p.project,v.label
              FROM memberships m JOIN target_projects p ON p.target_id=m.target_id
              JOIN versions v ON v.id=m.current_version JOIN targets t ON t.id=m.target_id
              WHERE m.document_id IN (""" + selected + ") AND alden_source_allowed(m.target_id,m.document_id,m.current_version)=1 AND p.permission!='denied' AND p.project IN (" + marks + ") "
            if self.schema >= 3:
                contexts_sql += "AND m.availability='available' "
            contexts_sql += """AND (? IS NULL OR m.target_id=?) AND (? IS NULL OR t.platform=?)
              AND (? IS NULL OR json_extract(v.metadata,'$.kind')=?)
              AND (? IS NULL OR v.collected_at>=?) AND (? IS NULL OR v.collected_at<?)
              ORDER BY m.document_id,p.project,m.target_id,m.current_version"""
            for association in db.execute(contexts_sql, (*ids, *projects, target_id, target_id, platform, platform,
                                                         node_type, node_type, since, since, until, until)):
                if _references_only:
                    continue  # Keep the SQL/error boundary without retaining display contexts.
                source_contexts.setdefault(association["document_id"], []).append({
                    "project": association["project"], "target_id": association["target_id"],
                    "version": association["current_version"], "label": association["label"]})
            nodes = []
            for row in rows:
                meta = json.loads(row["metadata"]) if isinstance(row["metadata"], str) else row["metadata"]
                kind = meta.get("kind")
                kind = kind[:256] if isinstance(kind, str) and kind else "document"
                description = row["body"][:2400]
                space = ", ".join(target_spaces.get(row["target_id"], []))
                evidence_class = row.get('content_evidence') or content_evidence_class(meta,row['body'])
                if _references_only:
                    # Identical ordered IDs/versions/targets and validation to
                    # the displayed page; no alternate sampling or authority.
                    nodes.append({"id": row["id"], "source_version": row["visible_version"],
                                  "source_target": row["target_id"]})
                    continue
                nodes.append({"id": row["id"], "label": row["label"] or row["original_id"], "category": kind,
                              "importance": 20, "updated_at": row["collected_at"], "description": description,
                              "space": space, "source_version": row["visible_version"],
                              "source_url": row["source_url"], "source_target": row["target_id"], "source_platform": row["platform"],
                              "source_contexts": source_contexts.get(row["id"], []),
                              "degree": row["degree"], "degree_scope": "permitted filtered graph",
                              "source_metadata": {"kind": kind, "content_evidence": evidence_class}, "evidence": {"kind": "snapshot", "source_event_ids": [row["original_id"]], "chat_id": "", "confirmed_at": None, "retracted": False}})
            edges = [{"id": row["id"], "source": row["source"], "target": row["target"], "relation": row["type"], "context": "explicit source relationship", "weight": 1, "purpose": "reference", "evidence_message_id": row["id"], "evidence": {"kind": "snapshot", "source_event_ids": [row["id"]], "chat_id": "", "confirmed_at": None, "retracted": False}}
                     for row in edge_rows]
            if draw_topical_edges:
                topical = [edge for edge in edges if edge["relation"] not in {"contains", "in-group", "imports", "generated-import"}]
                if topical:
                    edges = overview_draw_edges(topical, nodes)
            result = {"ok": True, "nodes": nodes, "edges": edges, "total_nodes": total, "total_edges": total_edges,
                    "next": offset+limit if more else None, "focus": focus, "scope": projects,
                    "facets": facets, "targets": self.targets(projects),
                    "revision": (db.execute("SELECT e.sequence FROM events e WHERE EXISTS(SELECT 1 FROM target_projects p WHERE p.target_id=e.target_id AND p.permission!='denied' AND p.project IN (" + marks + ")) ORDER BY e.sequence DESC LIMIT 1", projects).fetchone() or [0])[0],
                    "display_scope": "bounded derived projection; not full graph", "activity_checkpoint": checkpoint}
            if _references_only:
                return {key: result[key] for key in ('ok', 'nodes', 'total_nodes', 'total_edges', 'next', 'revision')} | {
                    'projection': 'current_graph_references'}
            if details and focus:
                row = next(r for r in rows if r["id"] == focus)
                detail = self._record_details(db, row, expected_version)
                if detail["ok"] is not True:
                    return detail
                result["details"] = detail["details"]
            return result

    def _record_details(self, db, row, expected_version) -> dict:
        if not db.execute('SELECT alden_source_allowed(?,?,?)',
                          (row['target_id'], row['id'], row['visible_version'])).fetchone()[0]:
            return {'ok': False, 'error': 'collection_source_authorization_required'}
        if expected_version is not None and row["visible_version"] != expected_version:
            return {"ok": False, "error": "collection_graph_version_changed"}
        raw = self._verified_source_blob(self.blobs, row["raw_path"], MAX_RECORD_BYTES)
        if digest(raw) != row["raw_sha256"]:
            raise RuntimeError("collection_source_integrity")
        record = json.loads(raw)
        retained = record.get("localOriginalText") if row["platform"] == "graph" and isinstance(record, dict) else None
        if isinstance(retained, str) and retained.strip():
            body, body_format = retained, "retained_original_text"
        else:
            body, body_format = row["body"], "normalized_text"
        projected = project_source_text(body, record)
        body = projected['body']
        # Structured raw data remains recoverable through its verified archive;
        # implementation metadata is not the reading experience.
        display_label = row["label"]
        if not display_label and isinstance(record, dict):
            display_label = record.get("title") or record.get("name")
            if not isinstance(display_label, str) or not display_label.strip():
                display_label = " · ".join(str(record[key]) for key in ("handle", "author", "date") if record.get(key))
        if not display_label:
            display_label = row["original_id"]
        return {"ok": True, "details": {"node_id": row["id"], "basis": "source_record", "summary": row["label"], "display_label": display_label,
                                     "body": body[:12000], "truncated": len(body) > 12000,
                                     "body_format": body_format,
                                     "text_projection": projected['projection'],
                                     "reading_projection": reading_tail_presentation(body, record),
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

    def recent_events(self, *, before=None, after=None, stream_id=None, limit=50, projects=None,
                      target_id=None, platform=None, stage=None, query="", since=None, until=None) -> dict:
        if (type(limit) is not int or not 1 <= limit <= 200
            or (before is not None and (type(before) is not int or before <= 0))
            or (after is not None and (type(after) is not int or after < 0))
            or (before is not None and after is not None)):
            raise ValueError("collection_event_cursor_invalid")
        if stream_id is not None and (not isinstance(stream_id, str) or not 1 <= len(stream_id) <= 128):
            raise ValueError("collection_event_cursor_invalid")
        if stage is not None and stage not in STAGES:
            raise ValueError("collection_stage_invalid")
        if platform is not None and platform not in PLATFORMS:
            raise ValueError("collection_platform_invalid")
        for value in (since, until):
            if value is not None and (type(value) not in {int, float} or not math.isfinite(value) or value < 0):
                raise ValueError("collection_event_time_invalid")
        if since is not None and until is not None and since >= until:
            raise ValueError("collection_event_time_invalid")
        if projects is None:
            projects = [p["project"] for p in self.projects()]
        # Shared scope/checkpoint and page/summary are one read transaction.
        # Filters remain stable for a frontend cursor; changing one resets it.
        clauses = []; args = []
        if stage:
            clauses.append("e.stage=?");args.append(stage)
        if since is not None:
            clauses.append("e.at>=?");args.append(since)
        if until is not None:
            clauses.append("e.at<?");args.append(until)
        if query:
            clauses.append("(t.label LIKE ? OR v.label LIKE ?)");args += ["%"+query+"%"]*2
        with self.database() as db:
            checkpoint, _, scope, scope_args = self._activity_scope(db, projects, target_id, platform)
            guarded = bool(db.execute('SELECT alden_has_source_guards()').fetchone()[0])
            reset = (stream_id is not None and stream_id != checkpoint['stream_id']
                     or after is not None and after > checkpoint['cursor'])
            if not reset and after is not None and guarded:
                reset = bool(db.execute("""SELECT 1 FROM events e JOIN targets t ON t.id=e.target_id
                    JOIN runs r ON r.id=e.run_id WHERE """ + scope + """ AND e.sequence>? AND e.sequence<=?
                    AND t.platform IN ('kakaotalk','telegram') AND e.stage='stored' AND r.state='complete'
                    AND json_extract(e.details,'$.change')='removed' LIMIT 1""",
                    (*scope_args, after, checkpoint['cursor'])).fetchone())
            if reset:
                before = after = None
            where = " AND ".join([scope, *clauses])
            if guarded:
                where += " AND alden_source_allowed(e.target_id,e.document_id,e.version)=1"
            args = [*scope_args, *args]
            # Preserve keyset order on the integer primary key. A target IN
            # index otherwise makes SQLite join/sort the entire journal for
            # a 50-row overview after the target endpoint index is installed.
            joins = " FROM events e NOT INDEXED JOIN targets t ON t.id=e.target_id LEFT JOIN versions v ON v.id=e.version LEFT JOIN runs r ON r.id=e.run_id "
            summary = None
            if before is None and after is None:
                summary = {"total": 0, "stages": {s: 0 for s in sorted(STAGES)},
                           "changes": {s: 0 for s in ('added', 'revised', 'unchanged', 'removed', 'relations_changed')},
                           "as_of": checkpoint['cursor']}
                accelerated = not guarded and not query and since is None and until is None and db.execute("SELECT 1 FROM meta WHERE key='journal_counts_v1' AND value='1'").fetchone()
                if accelerated:
                    count_query = "SELECT e.stage,e.change,SUM(e.count) AS count FROM event_counts e WHERE " + where + " GROUP BY e.stage,e.change"
                else:
                    summary_joins = " FROM events e JOIN targets t ON t.id=e.target_id LEFT JOIN versions v ON v.id=e.version " if query else " FROM events e "
                    count_query = "SELECT e.stage,CASE WHEN e.stage='stored' THEN json_extract(e.details,'$.change') END AS change,COUNT(*) AS count" + summary_joins + "WHERE " + where + " GROUP BY e.stage,change"
                for group in db.execute(count_query, args):
                    summary['total'] += group['count']
                    if group['stage'] in summary['stages']:
                        summary['stages'][group['stage']] += group['count']
                    if group['stage'] == 'stored' and group['change'] in summary['changes']:
                        summary['changes'][group['change']] += group['count']
            order = "ASC" if after is not None else "DESC"
            page_where, page_args = where, list(args)
            if before is not None:
                page_where += " AND e.sequence<?"; page_args.append(before)
            if after is not None:
                page_where += " AND e.sequence>?"; page_args.append(after)
            rows = db.execute("SELECT e.*,r.origin,r.state AS run_state,t.label AS target_label,t.platform,v.label AS document_label,v.metadata" + joins + "WHERE " + page_where + " ORDER BY e.sequence " + order + " LIMIT ?", (*page_args,limit+1)).fetchall()
            items = []
            for row in rows[:limit]:
                item = dict(row);item["details"] = json.loads(item["details"])
                meta = json.loads(item.pop("metadata") or "{}")
                item["source_url"] = meta.get("url", "")
                item["projects"] = [x[0] for x in db.execute("SELECT project FROM target_projects WHERE target_id=? AND permission!='denied' ORDER BY project", (item["target_id"],)) if x[0] in projects]
                items.append(item)
            return {"ok": True, "items": items, "next": items[-1]["sequence"] if len(rows)>limit else None,
                    "cursor": items[-1]["sequence"] if after is not None and len(rows)>limit else checkpoint['cursor'],
                    "stream_id": checkpoint['stream_id'], "reset": reset, "summary": summary}

    @staticmethod
    def _activity_scope(db, projects, target_id=None, platform=None):
        first = db.execute("SELECT event_id FROM events ORDER BY sequence LIMIT 1").fetchone()
        journal = db.execute("SELECT value FROM meta WHERE key='journal_id'").fetchone()
        if journal is None:
            generation = identity("stream", str(SCHEMA) + ":" + (first[0] if first else "empty"))
        else:
            anchor = db.execute("SELECT value FROM meta WHERE key='journal_first_event'").fetchone()
            if not re.fullmatch(r'[0-9a-f]{32}', journal[0]) or anchor is None:
                raise RuntimeError('collection_journal_identity_invalid')
            retained = first[0] if first else ''
            suffix = '' if retained == anchor[0] else ':pruned:' + (retained or 'empty')
            generation = identity("stream", journal[0] + suffix)
        marks = ",".join("?" for _ in projects) or "NULL"
        # Materialize the small permitted target set once, rather than running
        # a correlated permission lookup for every retained journal stage.
        where = """e.target_id IN (SELECT p.target_id FROM target_projects p
          JOIN targets t ON t.id=p.target_id WHERE p.permission!='denied'
          AND p.project IN (""" + marks + """ ) AND (? IS NULL OR p.target_id=?)
          AND (? IS NULL OR t.platform=?))"""
        args = [*projects, target_id, target_id, platform, platform]
        allowed = db.execute("SELECT DISTINCT p.target_id FROM target_projects p JOIN targets t ON t.id=p.target_id WHERE p.permission!='denied' AND p.project IN (" + marks + ") AND (? IS NULL OR p.target_id=?) AND (? IS NULL OR t.platform=?) ORDER BY p.target_id", args).fetchall()
        source_scopes = [(target[0], db.execute('SELECT alden_source_scope(?)', (target[0],)).fetchone()[0])
                         for target in allowed]
        source_scopes = [value for value in source_scopes if value[1] is not None]
        if source_scopes:
            generation = identity('stream', encoded([generation, source_scopes]).decode())
        # Endpoints need two bounded index lookups, not a full MIN+MAX scan.
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name='event_target_sequence'").fetchone():
            starts, ends = [], []
            for target in allowed:
                first_scoped = db.execute("SELECT sequence FROM events WHERE target_id=? ORDER BY sequence LIMIT 1", (target[0],)).fetchone()
                last_scoped = db.execute("SELECT sequence FROM events WHERE target_id=? ORDER BY sequence DESC LIMIT 1", (target[0],)).fetchone()
                if first_scoped: starts.append(first_scoped[0])
                if last_scoped: ends.append(last_scoped[0])
            earliest, latest = min(starts, default=0), max(ends, default=0)
        else:
            first_scoped = db.execute("SELECT e.sequence FROM events e WHERE " + where + " ORDER BY e.sequence LIMIT 1", args).fetchone()
            last_scoped = db.execute("SELECT e.sequence FROM events e WHERE " + where + " ORDER BY e.sequence DESC LIMIT 1", args).fetchone()
            earliest, latest = first_scoped[0] if first_scoped else 0, last_scoped[0] if last_scoped else 0
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
        if platform is not None and platform not in PLATFORMS:
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
            items, refresh_required = [], False
            for row in rows[:limit]:
                details = json.loads(row["details"])
                if (row["stage"] not in {"stored", "published"} or row["run_state"] != "complete"
                    or details.get("change") not in {"added", "revised", "removed", "relations_changed"}):
                    continue
                if row['stage'] == 'stored' and details.get('activity_deferred') is True:
                    continue
                if row['origin'].startswith('messenger-policy:'):
                    refresh_required = True
                    continue
                if not db.execute('SELECT alden_source_allowed(?,?,?)',
                    (row['target_id'],row['document_id'],row['version'])).fetchone()[0]:
                    refresh_required |= details['change'] in {'removed','relations_changed'}
                    continue
                items.append({"sequence": row["sequence"], "event_id": row["event_id"],
                              "document_id": row["document_id"], "version": row["version"],
                              "at": row["at"], "target_id": row["target_id"], "run_id": row["run_id"],
                              "origin": row["origin"], "kind": details["change"], "success": True})
            return {"ok": True, "items": items, "cursor": rows[min(len(rows),limit)-1]["sequence"] if rows else after,
                    "latest": latest, "stream_id": generation, "reset": False, "has_more": len(rows)>limit,
                    "refresh_required": refresh_required}


def read_action(state_root: Path, action: str, query: str | None = None, *,
                source_access=None, source_revision=None) -> dict:
    """Bounded read-only application boundary; never creates a store on lookup."""
    if action not in {"collection-projects", "collection-history", "collection-graph"}:
        raise ValueError("collection_action_invalid")
    options = json.loads(query) if query else {}
    if not isinstance(options, dict):
        raise ValueError("collection_query_invalid")
    store = CollectionStore.open_existing(state_root, source_access=source_access, source_revision=source_revision)
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
                                     stream_id=options.get("stream_id"),
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
        with store.database() as db:
            authority = store._source_authority_snapshot(db, projects)
        result = store.graph_page(projects=projects, limit=options.get("limit",120), offset=options.get("offset",0),
                                focus=options.get("focus"), hops=options.get("hops",1), query=str(options.get("search", ""))[:256],
                                target_id=options.get("target_id"), platform=options.get("platform"),
                                node_type=options.get("node_type"), relation=options.get("relation"),
                                since=options.get("since"), until=options.get("until"),
                                details=options.get("details",False), expected_version=options.get("expected_version"), overview=options.get("overview",False))
        # Read existing project-bound decisions without changing their source
        # validation or write contract. A union preserves independent contexts
        # unless every displayed current context explicitly agrees.
        if projects:
            from alden_entity_resolution import active_resolutions
            from alden_entity_projection import project_groups, project_context_groups
            with store.database() as db:
                groups = [group for project in dict.fromkeys(projects) for group in active_resolutions(store, db, [project])]
            current = [{**group, "state": "active"} for group in groups
                       if group.get("current_validity", {}).get("valid") is True]
            result = project_groups(result, current, projects[0]) if len(projects) == 1 else project_context_groups(result, current, projects)
        with store.database() as db:
            if store._source_authority_snapshot(db, projects) != authority:
                raise RuntimeError('collection_source_authorization_changed')
        return result
    raise ValueError("collection_action_invalid")
