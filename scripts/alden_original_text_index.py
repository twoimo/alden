"""Reversible retained-original lexical candidates, never source authority.

The caller owns the collection-dense-retrieval writer lock and the RW connection
to store.root/retrieval.sqlite3. No other cache tables or vector policies change.
CollectionStore owns all original I/O and permission/integrity decisions through
_original_text_inputs(projects), _original_text_session(),
_original_text_material(*, projects, node_id, target_id, expected_version,
                        _session=None), and
_verify_original_text_context(context, projects). Input entries are mappings with
document_id/target_id/version or three-tuples in that order. Session capabilities
and returned _context objects are private Python values, never JSON inputs.

file_original_meta is a key/value policy receipt; file_original_text contains
one current row per (target_id, document_id), including complete normalized body;
file_original_fts is external-content FTS5 over that row's label/body. Only these
tables and FTS5's own shadow tables are created. candidate_sql returns advisory
metadata, with no ORDER BY/LIMIT: a caller must join current authority first.
scoped_original_candidates performs that admission itself and returns a list;
an empty list cannot establish original-index coverage. Lookup never refreshes.

Proof offsets are Unicode scalar offsets into the whole normalized projection.
selection is {method, query_sha256}; method is fts5-snippet, korean-literal, or
integer-literal. No cache rank/body is a proof. The caller must fresh-guard all
selected _context values again immediately before publishing its final result.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import stat
import unicodedata

from alden_abort import AldenCancelled
from alden_collection import (BODY_NORMALIZATION_VERSION, _READ_CANCELLATION, encoded, integer_digit_matches,
    integer_digit_query, korean_literal_matches, korean_literal_plan, normalized_body)
from alden_file_set import SourceBodyError


POLICY = 'retained-original-search-v1'
ORIGINAL_TEXT_POLICY = POLICY
MAX_DOCUMENTS = 100000
MAX_TEXT_BYTES = 32 * 1024 * 1024
MAX_INPUT_BYTES = 5 * 1024 * 1024
_ALIAS = 'original_search'
_ATTACHMENT_SEAL = object()
_ERRORS = frozenset({'collection_original_index_invalid',
    'collection_original_index_stale', 'collection_original_index_budget'})
_BINDING = ('document_id', 'target_id', 'version', 'raw_sha256', 'input_sha256')
_CAPTURE = ('manifest_sha256', 'source_sha256')
_FIELDS = ('document_id', 'target_id', 'version', 'raw_sha256', 'input_sha256',
    'input_bytes', 'capture_manifest_sha256', 'capture_source_sha256',
    'config_sha256', 'projection_sha256')
_COLUMNS = ('id', *_FIELDS, 'label', 'projection_policy', 'characters', 'body')
_RECEIPT = {'schema': '1', 'policy': POLICY,
    'body_normalization': BODY_NORMALIZATION_VERSION}
_DDL = {
    'file_original_meta': 'CREATE TABLE file_original_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)',
    'file_original_text': '''CREATE TABLE file_original_text(
        id INTEGER PRIMARY KEY, document_id TEXT NOT NULL, target_id TEXT NOT NULL,
        version TEXT NOT NULL, raw_sha256 TEXT NOT NULL, input_sha256 TEXT NOT NULL,
        input_bytes INTEGER NOT NULL, capture_manifest_sha256 TEXT NOT NULL,
        capture_source_sha256 TEXT NOT NULL, config_sha256 TEXT NOT NULL,
        projection_sha256 TEXT NOT NULL, label TEXT NOT NULL,
        projection_policy TEXT NOT NULL, characters INTEGER NOT NULL, body TEXT NOT NULL,
        UNIQUE(target_id,document_id))''',
    'file_original_fts': '''CREATE VIRTUAL TABLE file_original_fts USING fts5(
        label,body,content='file_original_text',content_rowid='id',
        tokenize='unicode61 remove_diacritics 2')''',
}
_START, _END, _ELLIPSIS = '\x01', '\x02', '\x03'
_BYTE_SCOPE = 'exact declared original file bytes in SHA256-keyed base64 blobs, with structural parse locations'


class OriginalTextIndexError(ValueError):
    def __init__(self, code='collection_original_index_invalid'):
        self.code = code if isinstance(code, str) and code in _ERRORS else 'collection_original_index_invalid'
        super().__init__(self.code)


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _check(cancelled):
    if cancelled():
        raise AldenCancelled('collection_cancelled')


def _parent_call(function, cancelled, *args, **kwargs):
    """Use the collection owner's existing cancellation context during its I/O."""
    inherited = _READ_CANCELLATION.get()
    callback = cancelled if inherited is None or inherited is cancelled else lambda: cancelled() or inherited()
    token = _READ_CANCELLATION.set(callback)
    try:
        _check(callback)
        return function(*args, **kwargs)
    finally:
        _READ_CANCELLATION.reset(token)


def _invalid():
    raise OriginalTextIndexError() from None


def _stale():
    raise OriginalTextIndexError('collection_original_index_stale') from None


def _scope(projects):
    if (not isinstance(projects, list) or not 1 <= len(projects) <= 16
            or any(not isinstance(p, str) or not p or len(p) > 256 for p in projects)
            or len(set(projects)) != len(projects)):
        _invalid()
    return list(projects)


def _hex(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def _identity(value):
    return isinstance(value, str) and re.fullmatch(r'[\x21-\x7e]{1,256}', value) is not None


def _candidate(value):
    if not isinstance(value, dict) or any(k not in value for k in _FIELDS):
        _invalid()
    if (any(not _identity(value[k]) for k in _FIELDS[:3])
            or any(not _hex(value[k]) for k in _FIELDS[3:] if k != 'input_bytes')
            or type(value['input_bytes']) is not int
            or not 0 <= value['input_bytes'] <= MAX_INPUT_BYTES):
        _invalid()
    return {k: value[k] for k in _FIELDS}


def _index_path(store):
    path = Path(store.root).absolute() / 'retrieval.sqlite3'
    # Reject both a linked index and linked ancestors; never resolve to another
    # root silently. The collection owner already validates its root as well.
    if path.resolve() != path:
        _invalid()
    if path.exists() and not stat.S_ISREG(path.stat().st_mode):
        _invalid()
    for suffix in ('-wal', '-shm', '-journal'):
        sidecar = Path(str(path) + suffix)
        if sidecar.is_symlink():
            _invalid()
    return path


def _ddl_signature(sql):
    return re.sub(r'\s+', '', sql).lower()


def _validate_schema(db, alias):
    if alias not in ('main', _ALIAS):
        _invalid()
    present = {row[0]: (row[1], row[2]) for row in db.execute(
        f"SELECT name,type,sql FROM {alias}.sqlite_master WHERE name IN (?,?,?)", tuple(_DDL))}
    if not present:
        return False
    if set(present) != set(_DDL):
        _invalid()
    for name, ddl in _DDL.items():
        kind, sql = present[name]
        if kind != 'table' or not isinstance(sql, str) or _ddl_signature(sql) != _ddl_signature(ddl):
            _invalid()
    if dict(db.execute(f'SELECT key,value FROM {alias}.file_original_meta')) != _RECEIPT:
        _invalid()
    # A trigger is not part of this schema; explicit per-row FTS updates own
    # atomicity, and unexpected trigger writes must never run during refresh.
    if db.execute(f"SELECT 1 FROM {alias}.sqlite_master WHERE type='trigger' "
            "AND tbl_name IN ('file_original_meta','file_original_text','file_original_fts')").fetchone():
        _invalid()
    db.execute(f'SELECT rowid FROM {alias}.file_original_fts LIMIT 0')
    return True


def _register_metadata_functions(db):
    def config_sha(raw):
        try:
            config = json.loads(raw)
            return _sha(encoded(config)) if isinstance(config, dict) else None
        except (ValueError, TypeError, UnicodeError, RecursionError):
            return None

    def capture_hash(raw, field):
        try:
            cursor = json.loads(raw)
            capture = cursor.get('source_capture') if isinstance(cursor, dict) else None
            if (not isinstance(capture, dict) or set(capture) != {'schema', 'manifest', 'sha256', 'byte_scope'}
                    or type(capture.get('schema')) is not int or capture['schema'] != 1
                    or capture.get('byte_scope') != _BYTE_SCOPE or not _hex(capture.get('sha256'))
                    or not isinstance(capture.get('manifest'), str)
                    or not re.fullmatch(r'[0-9a-f]{64}\.json', capture['manifest'])):
                return None
            if field == 'manifest':
                name = capture.get('manifest')
                return name[:-5] if isinstance(name, str) and re.fullmatch(r'[0-9a-f]{64}\.json', name) else None
            value = capture.get('sha256')
            return value if _hex(value) else None
        except (ValueError, TypeError, UnicodeError, RecursionError):
            return None

    db.create_function('alden_original_config_sha', 1, config_sha, deterministic=True)
    db.create_function('alden_original_capture_manifest', 1,
        lambda raw: capture_hash(raw, 'manifest'), deterministic=True)
    db.create_function('alden_original_capture_source', 1,
        lambda raw: capture_hash(raw, 'source'), deterministic=True)
    db.create_function('alden_original_integer_literal', 2,
        lambda body, term: int(integer_digit_matches('', body, term)), deterministic=True)

    def korean_match(body, plan_json):
        try:
            if not isinstance(plan_json, str) or len(plan_json) > 8192:
                return 0
            plan = json.loads(plan_json)
            return int(korean_literal_matches(plan, '', body))
        except (ValueError, TypeError, AttributeError, RecursionError):
            return 0
    db.create_function('alden_original_korean_literal', 2, korean_match, deterministic=True)


def _attested_original_path(store, db, attribute):
    """Recognize only the readonly alias prepared by this owner initialization."""
    receipt = getattr(store, attribute, None)
    if (not isinstance(receipt, tuple) or len(receipt) != 5
            or receipt[0] is not db or receipt[4] is not _ATTACHMENT_SEAL):
        return None
    path = _index_path(store)
    info = path.stat()
    if path != receipt[1] or (info.st_dev, info.st_ino) != receipt[2:4]:
        _stale()
    return path


def _bound_original_path(store, db):
    return _attested_original_path(store, db, '_original_index_attachment')


def _bind_original_index(store, db):
    """Owner initialization only, before its permanent ATTACH/write denial."""
    if db.in_transaction:
        _invalid()
    alias = attach_original_index(store, db)
    rows = [row for row in db.execute('PRAGMA database_list') if row[1] == _ALIAS]
    if rows:
        path = _index_path(store)
        if len(rows) != 1 or Path(rows[0][2]).absolute() != path:
            _invalid()
        info = path.stat()
        receipt = (db, path, info.st_dev, info.st_ino, _ATTACHMENT_SEAL)
        if alias is None:
            # Keep legacy owners main/temp-only. The checked absence receipt
            # prevents a later graph query attempting forbidden ATTACH.
            db.execute(f'DETACH DATABASE {_ALIAS}')
            store._original_index_absence = receipt
        else:
            store._original_index_attachment = receipt
    return alias


def attach_original_index(store, db):
    """Attach the existing cache with mode=ro; return its alias or None.

    No directories, migrations or index creation occur. A partial/unknown schema,
    linked path, corrupt SQLite file or already-used alias fails closed.
    """
    attached = False
    try:
        path = _index_path(store)
        if not path.exists():
            return None
        existing = [row for row in db.execute('PRAGMA database_list') if row[1] == _ALIAS]
        if existing:
            if (len(existing) != 1 or _bound_original_path(store, db) != path
                    or Path(existing[0][2]).absolute() != path):
                _invalid()
            if not _validate_schema(db, _ALIAS):
                return None
            _register_metadata_functions(db)
            return _ALIAS
        if _attested_original_path(store, db, '_original_index_absence') == path:
            return None
        db.execute(f'ATTACH DATABASE ? AS {_ALIAS}', (path.as_uri() + '?mode=ro',))
        attached = True
        if not _validate_schema(db, _ALIAS):
            # The caller may own an active main read transaction. Schema reads
            # acquire an attached snapshot that cannot be DETACHed there;
            # leave this readonly attachment until the owned connection closes.
            return None
        _register_metadata_functions(db)
        return _ALIAS
    except (sqlite3.Error, OSError, ValueError) as error:
        if attached:
            try:
                db.execute(f'DETACH DATABASE {_ALIAS}')
            except sqlite3.Error:
                pass
        if isinstance(error, OriginalTextIndexError):
            raise
        _invalid()


def candidate_sql(alias, terms, *, query=None):
    """Unbounded advisory FTS metadata SQL; bind authority before adding LIMIT."""
    if alias != _ALIAS or not isinstance(terms, str) or not terms or len(terms) > 8192:
        _invalid()
    if query is not None and (not isinstance(query, str) or len(query) > 1024):
        _invalid()
    fields = ','.join('x.' + key for key in _FIELDS)
    body_terms = 'body : (' + terms + ')'
    sql = f'''SELECT {fields},bm25(file_original_fts) AS rank
        FROM {alias}.file_original_fts JOIN {alias}.file_original_text x
        ON x.id=file_original_fts.rowid WHERE file_original_fts MATCH ?
        AND x.projection_policy=?'''
    parameters = (body_terms, BODY_NORMALIZATION_VERSION)
    plan = korean_literal_plan(terms) if query is not None else None
    integer = integer_digit_query(query)
    if plan or integer:
        function = 'alden_original_integer_literal' if integer else 'alden_original_korean_literal'
        literal = integer if integer else encoded(plan).decode()
        sql += f''' UNION ALL SELECT {fields},0.0 AS rank FROM {alias}.file_original_text x
            WHERE x.projection_policy=? AND {function}(x.body,?)=1
            AND x.id NOT IN (SELECT rowid FROM {alias}.file_original_fts WHERE file_original_fts MATCH ?)'''
        parameters += (BODY_NORMALIZATION_VERSION, literal, body_terms)
    return sql, parameters


def _material_metadata(material):
    required = {'binding', 'capture', 'input_bytes', 'data', 'text', 'label', 'config_sha256', '_context'}
    if not isinstance(material, dict) or not required <= set(material):
        _invalid()
    binding, capture = material['binding'], material['capture']
    if (not isinstance(binding, dict) or set(binding) != set(_BINDING)
            or not isinstance(capture, dict) or set(capture) != set(_CAPTURE)
            or not all(_hex(capture[k]) for k in _CAPTURE)
            or not isinstance(material['data'], bytes) or not isinstance(material['text'], str)
            or not isinstance(material['label'], str) or len(material['label']) > MAX_TEXT_BYTES
            or type(material['input_bytes']) is not int
            or material['input_bytes'] != len(material['data'])
            or not 0 <= material['input_bytes'] <= MAX_INPUT_BYTES):
        _invalid()
    try:
        if material['text'].encode('utf-8') != material['data']:
            _stale()
    except UnicodeError:
        _invalid()
    if _sha(material['data']) != binding.get('input_sha256'):
        _stale()
    if any(marker in material['text'] for marker in (_START, _END, _ELLIPSIS)):
        _invalid()
    body = normalized_body(material['text'])
    metadata = {**binding, 'input_bytes': material['input_bytes'],
        'capture_manifest_sha256': capture['manifest_sha256'],
        'capture_source_sha256': capture['source_sha256'],
        'config_sha256': material['config_sha256'], 'projection_sha256': _sha(body.encode())}
    return _candidate(metadata), body


def _fresh(store, context, projects, cancelled):
    _check(cancelled)
    if _parent_call(store._verify_original_text_context, cancelled, context, projects) is False:
        _stale()
    _check(cancelled)


def refresh_original_index(store, projects, db, *, cancelled=lambda: False):
    """Incrementally refresh verified whole originals under the caller's lock.

    Unsupported format/missing capture remain explicit partial coverage. Old rows
    are never blindly pruned. Each new/reused row gets a fresh context check;
    failures do not report ready. No embedding or original-path parsing occurs.
    """
    projects = _scope(projects)
    result = dict(state='empty', policy=POLICY, eligible=0, indexed=0, reused=0,
        unsupported=0, missing_capture=0, embeddings_generated=0)
    try:
        _check(cancelled)
        path = _index_path(store)
        main = next(row[2] for row in db.execute('PRAGMA database_list') if row[1] == 'main')
        if not main or Path(main).absolute() != path or db.in_transaction:
            _invalid()
        if not _validate_schema(db, 'main'):
            with db:
                for ddl in _DDL.values():
                    db.execute(ddl)
                db.executemany('INSERT INTO file_original_meta VALUES(?,?)', _RECEIPT.items())
        used, seen = 0, set()
        session = store._original_text_session()
        if not isinstance(session, dict):
            _invalid()
        for entry in _parent_call(store._original_text_inputs, cancelled, projects):
            _check(cancelled)
            if result['eligible'] >= MAX_DOCUMENTS:
                raise OriginalTextIndexError('collection_original_index_budget')
            if isinstance(entry, dict):
                document, target, version = (entry[k] for k in _FIELDS[:3])
            elif isinstance(entry, (list, tuple)) and len(entry) == 3:
                document, target, version = entry
            else:
                _invalid()
            if not all(_identity(v) for v in (document, target, version)) or (target, document) in seen:
                _invalid()
            seen.add((target, document))
            result['eligible'] += 1
            try:
                material = _parent_call(store._original_text_material, cancelled, projects=projects, node_id=document,
                    target_id=target, expected_version=version, _session=session)
            except SourceBodyError as error:
                if error.code in ('alden_source_body_format_unsupported', 'alden_source_body_encoding_unsupported'):
                    result['unsupported'] += 1
                    continue
                if error.code == 'alden_source_body_capture_unavailable':
                    result['missing_capture'] += 1
                    continue
                raise
            metadata, body = _material_metadata(material)
            if tuple(metadata[k] for k in _FIELDS[:3]) != (document, target, version):
                _stale()
            used += len(body.encode()) + len(material['label'].encode())
            if used > MAX_TEXT_BYTES:
                raise OriginalTextIndexError('collection_original_index_budget')
            values = (*metadata.values(), material['label'], BODY_NORMALIZATION_VERSION, len(body), body)
            with db:
                old = db.execute('SELECT ' + ','.join(_COLUMNS) +
                    ' FROM file_original_text WHERE target_id=? AND document_id=?', (target, document)).fetchone()
                unchanged = old is not None and tuple(old)[1:] == values
                if not unchanged:
                    if old is not None:
                        db.execute("INSERT INTO file_original_fts(file_original_fts,rowid,label,body) "
                            "VALUES('delete',?,?,?)", (old[0], old[11], old[14]))
                        db.execute('DELETE FROM file_original_text WHERE id=?', (old[0],))
                    rowid = db.execute('INSERT INTO file_original_text(' + ','.join(_COLUMNS[1:]) +
                        ') VALUES(' + ','.join('?' for _ in values) + ')', values).lastrowid
                    db.execute('INSERT INTO file_original_fts(rowid,label,body) VALUES(?,?,?)',
                        (rowid, material['label'], body))
                _fresh(store, material['_context'], projects, cancelled)
            result['reused' if unchanged else 'indexed'] += 1
        if result['eligible']:
            result['state'] = 'partial' if result['unsupported'] or result['missing_capture'] else 'ready'
        return result
    except (SourceBodyError, AldenCancelled, OriginalTextIndexError):
        raise
    except RuntimeError as error:
        if str(error) == 'collection_cancelled':
            raise AldenCancelled('collection_cancelled') from None
        if str(error) == 'collection_graph_version_changed':
            _stale()
        _invalid()
    except ValueError as error:
        if str(error) == 'collection_original_index_budget':
            raise OriginalTextIndexError('collection_original_index_budget') from None
        _invalid()
    except (sqlite3.Error, OSError, UnicodeError, TypeError, KeyError, AttributeError):
        _invalid()


def _terms(query):
    if not isinstance(query, str) or len(query) > 1024:
        _invalid()
    import auto_reply_knowledge_graph as kg
    return kg._fts_query_terms([query])


def merge_original_ranks(legacy, original, *, limit):
    """One combined budget; full-original ranks replace parser ranks per ID.

    BM25 scores from separate corpora are not comparable. Interleave source
    ranks, starting with the complete original projection, instead of filling
    the entire budget from the older parser before admitting new body matches.
    """
    if type(limit) is not int or not 1 <= limit <= 140:
        _invalid()
    def unique(rows):
        seen, result = set(), []
        for identity, score in rows:
            if (not _identity(identity) or type(score) not in (int, float) or not math.isfinite(score)):
                _invalid()
            if identity not in seen:
                seen.add(identity); result.append((identity, float(score)))
        return result
    originals = unique(original)
    original_ids = {identity for identity, _ in originals}
    old = [row for row in unique(legacy) if row[0] not in original_ids]
    merged = []
    for position in range(max(len(originals), len(old))):
        for rows in (originals, old):
            if position < len(rows):
                merged.append(rows[position])
                if len(merged) == limit:
                    return merged
    return merged


def scoped_original_candidates(store, projects, query, *, limit=140,
        cancelled=lambda: False, time_from=None, time_to=None):
    """Current source admission BEFORE ordering/limit; never read original blobs.

    Source metadata/permission UDFs remain owned by CollectionStore. The capture
    manifest is hash-verified from its retained archive, not an original path.
    Cached ranks and IDs remain advisory; selected excerpts reverify all bytes.
    """
    projects = _scope(projects)
    if type(limit) is not int or not 1 <= limit <= 140:
        _invalid()
    clauses, times = [], []
    for value, operator in ((time_from, '>='), (time_to, '<=')):
        if value is not None:
            if type(value) not in (int, float) or not math.isfinite(value):
                _invalid()
            clauses.append('v.collected_at' + operator + '?')
            times.append(value)
    if time_from is not None and time_to is not None and time_from > time_to:
        _invalid()
    terms = _terms(query)
    _check(cancelled)
    if not terms or store.schema < 3:
        return []
    try:
        with store.database(cancelled=cancelled) as db:
            alias = attach_original_index(store, db)
            if alias is None:
                return []
            failures, manifests, checked_pairs = [], {}, set()

            def current_capture(target, document, version, raw, config, cursor, manifest_sha, source_sha, config_sha, input_sha):
                try:
                    _check(cancelled)
                    checked_pairs.add((target, document, version))
                    if len(checked_pairs) > MAX_DOCUMENTS:
                        raise OriginalTextIndexError('collection_original_index_budget')
                    if _sha(encoded(json.loads(config))) != config_sha:
                        return 0
                    checkpoint = json.loads(cursor or '{}')
                    capture = checkpoint.get('source_capture')
                    if (not isinstance(capture, dict) or set(capture) != {'schema', 'manifest', 'sha256', 'byte_scope'}
                            or type(capture.get('schema')) is not int or capture['schema'] != 1
                            or capture.get('byte_scope') != _BYTE_SCOPE
                            or capture.get('manifest') != manifest_sha + '.json'
                            or capture.get('sha256') != source_sha):
                        return 0
                    key = (target, manifest_sha, source_sha)
                    if key not in manifests:
                        manifests.clear()  # Keep only the last current receipt.
                        raw_manifest = _parent_call(store._source_body_archive, cancelled, Path(store.root) / 'source-captures',
                            capture['manifest'], 16 * 1024 * 1024)
                        manifest = json.loads(raw_manifest)
                        if (not isinstance(manifest, dict) or manifest.get('target_id') != target
                                or manifest.get('source_sha256') != source_sha
                                or manifest.get('source_file') != source_sha + '.json'
                                or manifest.get('adapter') != 'file-set'
                                or manifest.get('origin') != json.loads(config).get('path')
                                or not isinstance(manifest.get('records'), list)
                                or not isinstance(manifest.get('original_files'), list)):
                            _stale()
                        manifests[key] = manifest
                    manifest = manifests[key]
                    refs = [ref for ref in manifest['records'] if isinstance(ref, dict) and ref.get('document_id') == document
                        and ref.get('version') == version]
                    originals = [ref for ref in manifest['original_files'] if isinstance(ref, dict) and ref.get('sha256') == input_sha]
                    return int(len(refs) == 1 and refs[0].get('record_sha256') == raw and bool(originals))
                except (SourceBodyError, AldenCancelled, OriginalTextIndexError) as error:
                    failures.append(error)
                    return 0
                except (TypeError, KeyError, ValueError, AttributeError, OSError):
                    failures.append(OriginalTextIndexError())
                    return 0

            db.create_function('alden_original_current_capture', 10, current_capture)
            marks = ','.join('?' for _ in projects)
            prefix = f'''WITH original_visible AS MATERIALIZED (
                SELECT m.document_id,m.target_id,m.current_version,ROW_NUMBER() OVER(
                    PARTITION BY m.document_id ORDER BY v.collected_at DESC,m.target_id) AS choice
                FROM memberships m JOIN versions v ON v.id=m.current_version
                JOIN documents d ON d.id=m.document_id JOIN targets t ON t.id=m.target_id
                WHERE m.availability='available' AND d.availability='available'
                AND d.platform='files' AND t.platform='files'
                AND v.processing_version='declared-original-files-v1'
                AND alden_source_allowed(m.target_id,m.document_id,m.current_version)=1
                AND EXISTS(SELECT 1 FROM target_projects p WHERE p.target_id=m.target_id
                    AND p.permission!='denied' AND p.project IN ({marks}))
                ORDER BY m.target_id,m.document_id LIMIT {MAX_DOCUMENTS + 1}) '''
            scope = ''' JOIN original_visible s ON s.choice=1 AND s.document_id=c.document_id
                AND s.target_id=c.target_id AND s.current_version=c.version
                JOIN memberships m ON m.target_id=c.target_id AND m.document_id=c.document_id
                AND m.current_version=c.version JOIN versions v ON v.id=m.current_version
                AND v.document_id=m.document_id AND v.raw_sha256=c.raw_sha256
                JOIN documents d ON d.id=m.document_id JOIN targets t ON t.id=m.target_id
                WHERE d.availability='available' AND m.availability='available'
                AND d.platform='files' AND t.platform='files'
                AND v.processing_version='declared-original-files-v1'
                AND alden_source_allowed(m.target_id,m.document_id,m.current_version)=1
                AND alden_original_current_capture(m.target_id,m.document_id,m.current_version,
                    v.raw_sha256,t.config,t.cursor,c.capture_manifest_sha256,
                    c.capture_source_sha256,c.config_sha256,c.input_sha256)=1'''
            if clauses:
                scope += ' AND ' + ' AND '.join(clauses)
            count = db.execute(prefix + 'SELECT count(*) FROM original_visible', projects).fetchone()[0]
            if count > MAX_DOCUMENTS:
                raise OriginalTextIndexError('collection_original_index_budget')
            size = db.execute(prefix + f'''SELECT COALESCE(SUM(
                length(CAST(c.body AS BLOB))+length(CAST(c.label AS BLOB))),0)
                FROM {alias}.file_original_text c''' + scope, (*projects, *times)).fetchone()[0]
            if failures:
                raise failures[0]
            if size > MAX_TEXT_BYTES:
                raise OriginalTextIndexError('collection_original_index_budget')
            sql, args = candidate_sql(alias, terms, query=query)
            # Match the source owner's visible target/version before LIMIT.
            # One visible row and the cache's unique pair prevent duplicate IDs
            # from consuming slots. FTS and literal paths both admit first.
            fields = ','.join('c.' + key for key in _FIELDS)
            selected = []
            rows = db.execute(prefix + 'SELECT ' + fields + ',c.rank FROM (' + sql + ') c' + scope
                + ' ORDER BY c.rank,c.document_id,c.target_id LIMIT ?',
                (*projects, *args, *times, limit))
            for row in rows:
                _check(cancelled)
                if len(selected) >= MAX_DOCUMENTS:
                    raise OriginalTextIndexError('collection_original_index_budget')
                selected.append(dict(zip((*_FIELDS, 'rank'), tuple(row))))
            if failures:
                raise failures[0]
            _check(cancelled)
            ordered = sorted(selected, key=lambda row: (row['rank'], row['document_id'], row['target_id']))
            unique = {}
            for row in ordered:
                _candidate(row)
                if not isinstance(row['rank'], (int, float)) or not math.isfinite(row['rank']):
                    _invalid()
                unique.setdefault(row['document_id'], row)
            return list(unique.values())[:limit]
    except (SourceBodyError, AldenCancelled, OriginalTextIndexError):
        raise
    except RuntimeError as error:
        _check(cancelled)
        if str(error) == 'collection_cancelled':
            raise AldenCancelled('collection_cancelled') from None
        _stale()
    except (sqlite3.Error, OSError, ValueError, TypeError, AttributeError) as error:
        _check(cancelled)
        _invalid()


def _literal_span(body, query, terms):
    integer = integer_digit_query(query)
    if integer and integer_digit_matches('', body, integer):
        for match in re.finditer(r'\d+(?:[.,]+\d+)*', body):
            if match.group() == integer:
                return match.start(), match.end(), 'integer-literal'
    plan = korean_literal_plan(terms)
    if plan and korean_literal_matches(plan, '', body):
        for clause in plan:
            if not korean_literal_matches((clause,), '', body):
                continue
            for word in clause:
                for match in re.finditer(re.escape(word), body, re.IGNORECASE):
                    if ((not word[0].isdigit() or match.start() == 0 or not body[match.start()-1].isdigit())
                            and (not word[-1].isdigit() or match.end() == len(body) or not body[match.end()].isdigit())):
                        return match.start(), match.end(), 'korean-literal'
    return None


def _snippet_span(body, query, terms, cancelled):
    _check(cancelled)
    with closing(sqlite3.connect(':memory:')) as db:
        db.set_progress_handler(lambda: int(bool(cancelled())), 256)
        db.execute("CREATE VIRTUAL TABLE excerpt_fts USING fts5(body,tokenize='unicode61 remove_diacritics 2')")
        db.execute('INSERT INTO excerpt_fts VALUES(?)', (body,))
        row = db.execute('SELECT snippet(excerpt_fts,0,?,?,?,48) FROM excerpt_fts WHERE excerpt_fts MATCH ?',
            (_START, _END, _ELLIPSIS, terms)).fetchone()
    _check(cancelled)
    if row and _START in row[0] and _END in row[0]:
        marked = row[0].strip(_ELLIPSIS)
        plain = marked.replace(_START, '').replace(_END, '')
        base = body.find(plain)
        if base < 0 or _ELLIPSIS in plain:
            _stale()
        first = marked.index(_START)
        start = len(marked[:first].replace(_END, ''))
        finish = marked.index(_END, first)
        end = len(marked[:finish].replace(_START, '').replace(_END, ''))
        return base + start, base + end, 'fts5-snippet'
    literal = _literal_span(body, query, terms)
    if literal is None:
        _stale()  # A matching label or cached FTS slot is not a body match.
    return literal


def _original_range(original, data, start, end, text, cancelled):
    """Map exact endpoints, or disclose complete original endpoint lines for NFC."""
    raw_offset = projected_offset = 0
    first = last = None
    for match in re.finditer(r'[^\r\n]*(?:\r\n|\r|\n|$)', original):
        _check(cancelled)
        line = match.group()
        if not line:
            continue
        projected = normalized_body(line)
        raw_end = raw_offset + len(line.encode())
        projected_end = projected_offset + len(projected)
        item = (raw_offset, raw_end, projected_offset, projected_end, line)
        if first is None and projected_end > start:
            first = item
        if projected_end >= end:
            last = item
            break
        raw_offset, projected_offset = raw_end, projected_end
    if first is None or last is None:
        _stale()
    covering = any(unicodedata.normalize('NFC', item[4]) != item[4] for item in (first, last))
    if covering:
        raw_start, raw_end = first[0], last[1]
        mapping = 'covering_original_lines'
    else:
        def endpoint(item, offset):
            raw_start, raw_end, position, _finish, line = item
            local = offset - position
            content = line.rstrip('\r\n')
            return raw_start + len(content[:local].encode()) if local <= len(content) else raw_end
        raw_start, raw_end = endpoint(first, start), endpoint(last, end)
        mapping = 'exact_projection_span'
    selected = data[raw_start:raw_end]
    projection = normalized_body(selected.decode('utf-8'))
    if (mapping == 'exact_projection_span' and projection != text
            or mapping == 'covering_original_lines' and text not in projection):
        _stale()
    return dict(start_bytes=raw_start, end_bytes=raw_end, sha256=_sha(selected), mapping=mapping)


def _excerpt_terms(store, candidate, terms, cancelled):
    """Choose the strongest term contribution; cache scores are advisory only.

    A repeated common head token must not hide the rare term that selected this
    complete original. The actual span is still resolved by FTS over freshly
    verified material, never by trusting a cached body or cached coordinates.
    """
    tokens = re.findall(r'"(?:[^"]|"")*"', terms)
    if len(tokens) < 2:
        return terms
    _check(cancelled)
    path = _index_path(store)
    if not path.is_file():
        _stale()
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=.15)) as db:
        db.row_factory = sqlite3.Row
        db.set_progress_handler(lambda: int(bool(cancelled())), 256)
        if not _validate_schema(db, 'main'):
            _stale()
        row = db.execute('SELECT id,' + ','.join(_FIELDS) +
            ' FROM file_original_text WHERE target_id=? AND document_id=?',
            (candidate['target_id'], candidate['document_id'])).fetchone()
        if row is None or _candidate(dict(row)) != _candidate(candidate):
            _stale()
        scores = []
        for position, term in enumerate(tokens[:24]):
            _check(cancelled)
            score = db.execute('SELECT bm25(file_original_fts) FROM file_original_fts '
                'WHERE rowid=? AND file_original_fts MATCH ?', (row['id'], 'body : (' + term + ')')).fetchone()
            if score is not None:
                if type(score[0]) not in (int, float) or not math.isfinite(score[0]):
                    _invalid()
                scores.append((score[0], position, term))
        # Literal-only candidates retain the existing checked literal path.
        return min(scores)[2] if scores else terms


def resolve_original_excerpt(store, projects, candidate, query, *, cancelled=lambda: False,
        _session=None, max_characters=1200):
    """Resolve only from current verified material and an actual body query match."""
    projects = _scope(projects)
    expected, terms = _candidate(candidate), _terms(query)
    if type(max_characters) is not int or not 1 <= max_characters <= 1200 or not terms:
        _invalid()
    try:
        _check(cancelled)
        material = _parent_call(store._original_text_material, cancelled, projects=projects,
            node_id=expected['document_id'], target_id=expected['target_id'],
            expected_version=expected['version'], _session=_session)
        actual, body = _material_metadata(material)
        if actual != expected:
            _stale()
        anchor_terms = _excerpt_terms(store, expected, terms, cancelled)
        match_start, match_end, method = _snippet_span(body, query, anchor_terms, cancelled)
        # Include the whole selected match when it fits, and useful surrounding
        # source text. Offsets always refer to the whole document, never chunks.
        start = max(0, match_start - max(0, (max_characters - (match_end-match_start)) // 2))
        end = min(len(body), start + max_characters)
        start = max(0, end - max_characters)
        text = body[start:end]
        if not text or not (start <= match_start < end):
            _stale()
        original_range = _original_range(material['text'], material['data'], start, end, text, cancelled)
        proof = {'schema': 1, 'kind': 'verified-original-search-excerpt',
            'binding': dict(material['binding']), 'capture': dict(material['capture']),
            'input_bytes': material['input_bytes'],
            'projection': {'policy': BODY_NORMALIZATION_VERSION,
                'sha256': actual['projection_sha256'], 'characters': len(body)},
            'excerpt': {'start_character': start, 'end_character': end,
                'sha256': _sha(text.encode()), 'truncated': start > 0 or end < len(body)},
            'original_range': original_range,
            'selection': {'method': method, 'query_sha256': _sha(normalized_body(query).encode())}}
        _fresh(store, material['_context'], projects, cancelled)
        return {'text': text, 'proof': proof, '_context': material['_context']}
    except (SourceBodyError, AldenCancelled, OriginalTextIndexError):
        raise
    except RuntimeError as error:
        if str(error) == 'collection_cancelled':
            raise AldenCancelled('collection_cancelled') from None
        if str(error) == 'collection_graph_version_changed':
            _stale()
        _invalid()
    except (sqlite3.Error, OSError, UnicodeError, ValueError, TypeError, KeyError, AttributeError):
        _check(cancelled)
        _invalid()
