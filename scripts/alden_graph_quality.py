"""Read-only, current-version collection audit; candidates never authorize merging."""
from collections import Counter, defaultdict
from contextlib import closing, contextmanager, nullcontext
import argparse
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import struct
from threading import local
import time
import unicodedata

from alden_collection import CollectionStore, MAX_RECORD_BYTES, digest, encoded, identity, normalized_body, normalized_text, RELATION_HISTORY_MATCH
from alden_content_evidence import POLICY, content_evidence_class, declared_source_annotations, ANNOTATION_FIELDS
from alden_normalization_evidence import DeclaredFieldSummary
from alden_source_text import TEXT_VERSION, POLICY as SOURCE_TEXT_POLICY, project_source_text


def _quality_read_cache(db, schema):
    # Only these already read-only, derived DB connections are configured.
    # Mapping shares clean OS pages instead of duplicating the working set in
    # private SQLite buffers. Keep the previous cache if mapping is unavailable.
    fallback = {'main': 65536, 'quality_cache': 32768}[schema]
    admitted = db.execute(f'PRAGMA {schema}.mmap_size=268435456').fetchone()
    mapped = admitted is not None and type(admitted[0]) is int and admitted[0] >= 268435456
    kib = 8192 if mapped else fallback
    db.execute(f'PRAGMA {schema}.cache_size=-{kib}')


def _reject_json_constant(value):
    raise ValueError("nonfinite_json")


def _finite_json_float(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("nonfinite_json")
    return result


def _unique_json_object(pairs):
    result = {}
    for key, item in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = item
    return result


# Reuse parsing policy, not source objects. A decoder belongs to its calling
# thread; scanner internals are never shared by overlapping worker threads.
_JSON_DECODERS = local()


def _json(value):
    if isinstance(value, str):
        try:
            decoder = _JSON_DECODERS.decoder
        except AttributeError:
            decoder = _JSON_DECODERS.decoder = json.JSONDecoder(
                parse_constant=_reject_json_constant, parse_float=_finite_json_float,
                object_pairs_hook=_unique_json_object)
        return decoder.decode(value)
    # Preserve json.loads' bytes/bytearray encoding detection and type errors.
    return json.loads(value, parse_constant=_reject_json_constant,
        parse_float=_finite_json_float, object_pairs_hook=_unique_json_object)


def _substantive_field(value, *, title=False):
    # Only explicit missing-data markers are excluded. This heuristic never
    # certifies semantic completeness, identity or a merge decision.
    marker = normalized_text(value).casefold()
    if not marker or marker in {'...', '…', 'n/a', '(n/a)', '[n/a]', '(empty)', '[empty]'}:
        return False
    if title and (len(value) >= 1024 or marker in
                  {'untitled', '(untitled)', '[untitled]', 'no title', '제목 없음', '제목없음'}):
        return False
    return True


def _read_blob(parent, name):
    if not isinstance(name, str) or not re.fullmatch(r"[0-9a-f]{64}\.json", name):
        raise ValueError("quality_blob_name")
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    with os.fdopen(fd, "rb") as handle:
        before = os.fstat(handle.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_RECORD_BYTES:
            raise ValueError("quality_blob_shape")
        data = handle.read(MAX_RECORD_BYTES + 1)
        after = os.fstat(handle.fileno())
        if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
            raise ValueError("quality_blob_changed")
    if len(data) > MAX_RECORD_BYTES or digest(data) + ".json" != name:
        raise ValueError("quality_blob_hash")
    return data


def _blob(folder, name):
    if any(p.is_symlink() for p in [folder, *folder.parents]):
        raise ValueError("quality_blob_symlink")
    parent = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        return _read_blob(parent, name)
    finally:
        os.close(parent)


def _open_blob_directory(folder):
    folder = Path(folder)
    if not folder.is_absolute() or '..' in folder.parts:
        raise ValueError('quality_blob_name')
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(folder.anchor, flags)
    try:
        for component in folder.parts[1:]:
            child = os.open(component, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


@contextmanager
def _pinned_blobs(folder):
    """Anchor one scan to a no-symlink directory; recheck identity on success."""
    fd = _open_blob_directory(folder)
    active = True
    def read(name):
        if not active:
            raise ValueError('quality_blob_closed')
        return _read_blob(fd, name)
    try:
        before = os.fstat(fd)
        yield read
        current = _open_blob_directory(folder)
        try:
            after = os.fstat(current)
            if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                raise ValueError('quality_blob_changed')
        finally:
            os.close(current)
    finally:
        active = False
        os.close(fd)


def _annotation_facts(source):
    """Bounded retained declarations, not a verdict about the source body."""
    fields = declared_source_annotations(source)['fields']
    result = {}
    for key, field in fields.items():
        values = {unicodedata.normalize('NFC', value) for value in field['values']}
        result[key] = {'state': field['state'],
                       'signature': digest(encoded(sorted(values))) if field['state'] == 'declared' else None,
                       'repeated_values': len(field['values']) - len(values)}
    return result


def _annotation_review(by_member, body_groups, *, inspected, sample_cap, check, candidates=None):
    fields = {key: Counter({state: 0 for state in
        ('declared', 'undeclared', 'unsupported', 'over_budget', 'repeated_values',
         'body_groups_with_different_values')}) for key in ANNOTATION_FIELDS}
    repeated = 0
    for values in by_member.values():
        check()
        repeated += any(field['repeated_values'] for field in values.values())
        for key, field in values.items():
            fields[key][field['state']] += 1
            fields[key]['repeated_values'] += field['repeated_values']
    compared, different, members = 0, 0, 0
    samples, taken = [], Counter()
    for (project, body_hash), group in sorted(body_groups.items()):
        check()
        group = group.intersection(by_member)
        if len(group) < 2:
            continue
        compared_fields, different_fields = [], []
        for key in ANNOTATION_FIELDS:
            values = [by_member[member][key]['signature'] for member in group
                      if member in by_member and by_member[member][key]['state'] == 'declared']
            if len(values) >= 2:
                compared_fields.append(key)
                if len(set(values)) > 1:
                    different_fields.append(key)
                    fields[key]['body_groups_with_different_values'] += 1
        compared += bool(compared_fields)
        if different_fields:
            different += 1
            members += len(group)
            if candidates is not None:
                candidates.append({'project':project,'projected_body_sha256':body_hash,
                    'different_fields':different_fields,'members':len(group),
                    'source_versions':[{'document_id':member[1],'version':member[2]} for member in sorted(group)]})
            if taken[project] < sample_cap:
                samples.append({'project': project, 'projected_body_sha256': body_hash,
                    'different_fields': different_fields, 'members': len(group),
                    'document_ids': sorted({member[1] for member in group})[:sample_cap],
                    'source_versions': [{'document_id': member[1], 'version': member[2]}
                                        for member in sorted(group)[:sample_cap]]})
                taken[project] += 1
    return {'policy': 'retained-annotation-review-v1',
            'status': 'verified_retained_declarations' if inspected else 'not_inspected',
            'semantic_status': 'unverified',
            'comparison': 'NFC_exact_values_as_unordered_sets_preserving_case_and_spacing',
            'counts': {'source_versions_inspected': len(by_member),
                       'records_with_repeated_values': repeated,
                       'same_projected_body_groups_compared': compared,
                       'same_projected_body_groups_with_tag_differences': different,
                       'same_body_group_members_with_tag_differences': members},
            'fields': {key: dict(value) for key, value in fields.items()}, 'samples': samples}


def audit_annotations(root, projects, *, max_rows=100000, max_bytes=512*1024*1024,
                      timeout=20, sample_cap=0, cancelled=None, collect_candidates=False):
    """Source/version-bound tag review; no retrieval vectors or relations scan."""
    if (not isinstance(projects, (list, tuple)) or not 1 <= len(projects) <= 16
            or any(not isinstance(p, str) or not p or len(p) > 128 for p in projects)
            or type(max_rows) is not int or not 1 <= max_rows <= 100000
            or type(max_bytes) is not int or not 1 <= max_bytes <= 512*1024*1024
            or type(sample_cap) is not int or not 0 <= sample_cap <= 10
            or type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 120):
        raise ValueError('quality_budget_invalid')
    store = CollectionStore.open_existing(Path(root))
    if store is None or store.schema != 3:raise ValueError('quality_existing_v3_store_required')
    projects = sorted(set(projects)); started = time.monotonic(); next_cancel_check = started
    rows, bytes_read = 0, 0
    annotations, groups, cached, blobs, findings = {}, defaultdict(set), {}, {}, Counter()
    if type(collect_candidates) is not bool:raise ValueError('quality_budget_invalid')
    scope_digest=None
    def check():
        nonlocal next_cancel_check
        now = time.monotonic()
        if cancelled is not None and now >= next_cancel_check:
            next_cancel_check = now + .05
            if cancelled():raise RuntimeError('quality_cancelled')
        if now-started > timeout:raise RuntimeError('quality_time_budget')
        if rows > max_rows or bytes_read > max_bytes:raise RuntimeError('quality_scan_budget')
    with store.database() as db, _pinned_blobs(store.blobs) as read_blob:
        marks = ','.join('?' for _ in projects)
        scoped = db.execute('''WITH scoped AS (SELECT DISTINCT p.project,m.document_id,m.current_version
            FROM memberships m JOIN target_projects p ON p.target_id=m.target_id
            JOIN documents d ON d.id=m.document_id
            WHERE m.availability='available' AND d.availability='available'
            AND p.permission!='denied' AND p.project IN ('''+marks+'''))
            SELECT s.project AS scoped_project,s.document_id AS scoped_document,
                s.current_version AS scoped_version,d.platform,d.original_id,v.*
            FROM scoped s JOIN documents d ON d.id=s.document_id
            LEFT JOIN versions v ON v.document_id=d.id AND v.id=s.current_version
            ORDER BY s.project,s.document_id,s.current_version''',projects)
        for row in scoped:
            project, document, version = row['scoped_project'], row['scoped_document'], row['scoped_version']
            rows += 1;check()
            key = (document, version)
            if key not in cached:
                cached[key] = None
                if row['id'] is None:
                    findings['version_identity_mismatch'] += 1;continue
                try:
                    bytes_read += sum(len(v.encode()) for v in
                        (row['metadata'], row['label'], row['body']) if isinstance(v, str));check()
                    metadata = _json(row['metadata'])
                    if not isinstance(metadata, dict):raise ValueError('metadata')
                    projection = digest(encoded([row['label'],row['body'],
                        {k:v for k,v in metadata.items() if k != 'source'}]))
                    expected = identity('version',encoded([document,row['raw_sha256'],
                        row['processing_version'],projection]).decode())
                    if (identity(row['platform'],row['original_id']) != document or expected != version
                            or projection != row['projection_sha256']):
                        findings['version_identity_mismatch'] += 1;continue
                    name = row['raw_path']
                    if not isinstance(name,str) or name != row['raw_sha256']+'.json':
                        findings['source_reference_invalid'] += 1;continue
                    if name not in blobs:
                        blobs[name] = None
                        try:
                            raw = read_blob(name);bytes_read += len(raw);check()
                            source = _json(raw)
                            original = source.get('localOriginalText') if isinstance(source,dict) else None
                            projected = project_source_text(original,source)['body'] if isinstance(original,str) and original.strip() else None
                            declarations = {key:source[key] for key in ('kind','content_indexed')
                                if isinstance(source,dict) and key in source}
                            blobs[name] = (_annotation_facts(source),declarations,projected)
                        except (OSError,ValueError,RecursionError):
                            findings['source_blob_invalid'] += 1
                    if blobs[name] is None:continue
                    facts, source, projected = blobs[name]
                    body = projected if projected is not None else row['body']
                    evidence = content_evidence_class(metadata,body,source=source,
                        body_source='retained_record.localOriginalText' if projected is not None else 'stored_body')
                    if evidence in ('content_candidate', 'reference_only'):
                        cached[key] = (facts,digest(projected.encode()) if projected is not None else None)
                except (ValueError,TypeError,RecursionError):
                    findings['metadata_invalid'] += 1
            if cached[key] is not None:
                facts, body_hash = cached[key];member = (project,document,version)
                annotations[member] = facts
                if body_hash is not None:groups[(project,body_hash)].add(member)
        if collect_candidates:
            from alden_annotation_candidates import scope_fingerprint
            scope_digest=scope_fingerprint(db,projects,check)
    check();candidates=[] if collect_candidates else None
    result={'schema':1,'mode':'read_only_source_annotations','projects':projects,
        'source_verification_requested':True,'findings':dict(findings),
        'source_annotation_review':_annotation_review(annotations,groups,inspected=True,sample_cap=sample_cap,check=check,candidates=candidates),
        'coverage':{'status':'complete','source_verification':'retained_canonical_json',
            'scope':'current_permitted_memberships_only','versions_scanned':rows,'bytes_examined':bytes_read},
        'elapsed_s':time.monotonic()-started}
    if collect_candidates:result.update(candidate_groups=candidates,scope_digest=scope_digest)
    return result


def audit(root, projects, *, verify_sources=False, max_rows=100000,
          max_bytes=512 * 1024 * 1024, timeout=120, sample_cap=5, cancelled=None):
    if (not isinstance(projects, (list, tuple)) or not 1 <= len(projects) <= 16
            or any(not isinstance(p, str) or not p or len(p) > 128 for p in projects)):
        raise ValueError("quality_project_scope_required")
    if (type(max_rows) is not int or not 1 <= max_rows <= 100000
            or type(max_bytes) is not int or not 1 <= max_bytes <= 512 * 1024 * 1024
            or type(sample_cap) is not int or not 0 <= sample_cap <= 10
            or type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 120):
        raise ValueError("quality_budget_invalid")
    store = CollectionStore.open_existing(Path(root))
    if store is None or store.schema != 3:
        raise ValueError("quality_existing_v3_store_required")
    started = time.monotonic()
    next_cancel_check = started
    projects = sorted(set(projects))
    counts, findings, samples = Counter(), Counter(), defaultdict(list)
    raw_groups, raw_entries = defaultdict(set), defaultdict(set)
    text_entries, metadata_entries = defaultdict(set), defaultdict(set)
    body_entries, reference_entries = defaultdict(set), defaultdict(set)
    body_counts = Counter()
    allowed, checked_blobs = defaultdict(set), set()
    membership_contexts, source_tags, scoped_versions = defaultdict(set), {}, defaultdict(set)
    seen, profiles, originals, source_verified = set(), defaultdict(Counter), {}, {}
    profile_hashes = {}
    source_declarations = {}
    source_annotations, member_annotations = {}, {}
    degree, directed_relations = defaultdict(Counter), Counter()
    declared_fields = DeclaredFieldSummary()
    projected_originals, numeric_tails, projected_body_entries = {}, set(), defaultdict(set)

    def check():
        nonlocal next_cancel_check
        now = time.monotonic()
        if cancelled is not None and now >= next_cancel_check:
            next_cancel_check = now + .05
            if cancelled():
                raise RuntimeError('quality_cancelled')
        if now - started > timeout:
            raise RuntimeError("quality_time_budget")
        if counts['memberships'] + counts['relations'] > max_rows or counts['bytes_examined'] > max_bytes:
            raise RuntimeError("quality_scan_budget")

    def flag(kind, key):
        findings[kind] += 1
        if len(samples[kind]) < sample_cap:
            samples[kind].append(key)

    def examine(*values):
        # Count malformed BLOBs too, before any row can be skipped.
        size = 0
        for value in values:
            if isinstance(value, str):
                size += len(value.encode())
            elif isinstance(value, bytes):
                size += len(value)
        counts['bytes_examined'] += size
        check()

    retrieval = store.root / 'retrieval.sqlite3'
    if any(p.is_symlink() for p in [retrieval, *retrieval.parents]):
        raise ValueError('quality_retrieval_symlink')
    cache_context = closing(sqlite3.connect(retrieval.as_uri() + '?mode=ro', uri=True, timeout=.15)) if retrieval.is_file() else nullcontext(None)
    with store.database() as db, cache_context as cache:
        # Request-local read settings for derived stores only. No persistent
        # database setting, original DB or model allocation is changed.
        _quality_read_cache(db, 'main')
        if cache is not None:
            cache.execute('PRAGMA query_only=ON'); cache.execute('BEGIN')
        cache_tables = {r[0] for r in cache.execute("SELECT name FROM sqlite_master WHERE type='table'")} if cache else set()
        # Stream the exact current-version cache rows with the source cursor.
        # Two per-record lookups previously repeated over 53,000 SQL calls.
        # The attached database is explicitly read-only; no cache migration or
        # all-record Python map is created. Existing row validation still runs.
        cache_fields={'text':('base_hash','raw_sha256','extraction','body','body_source'),
                      'vector':('text_hash','model','endpoint','encoding','vector')}
        extras=[];joins=[]
        if cache_tables.intersection({'texts','vectors'}):
            db.execute('ATTACH DATABASE ? AS quality_cache',(retrieval.as_uri()+'?mode=ro',))
            _quality_read_cache(db, 'quality_cache')
        eligible="""typeof(m.document_id)='text' AND typeof(m.current_version)='text'
            AND d.platform IS NOT NULL AND d.availability='available'
            AND v.document_id=m.document_id AND typeof(v.label)='text' AND typeof(v.body)='text'
            AND typeof(v.raw_sha256)='text' AND typeof(v.raw_path)='text'
            AND typeof(v.processing_version)='text' AND typeof(v.projection_sha256)='text'"""
        for kind,table in [('text','texts'),('vector','vectors')]:
            alias='quality_'+kind
            present=table in cache_tables
            columns=('document_id',*cache_fields[kind])
            extras.extend((f'{alias}.{column}' if present else 'NULL')+f' AS {alias}_{column}' for column in columns)
            if present:
                joins.append(f' LEFT JOIN quality_cache.{table} AS {alias} ON {alias}.document_id=m.document_id '
                    f'AND {alias}.version=m.current_version AND ({eligible})')
        marks = ','.join('?' for _ in projects)
        def scoped_rows():
            contexts=db.execute("SELECT project,permission,target_id FROM target_projects WHERE permission!='denied' "
                "AND project IN ("+marks+") ORDER BY project,target_id",projects)
            for project,permission,target_id in contexts:
                check()
                # Keep the original project/target/document order without a
                # wide temp sort containing all cached bodies and vectors.
                yield from db.execute('''SELECT ? AS project,? AS permission,m.target_id,m.document_id,m.current_version,
                    d.platform,d.original_id,d.availability AS document_availability,
                    v.document_id AS version_document,v.raw_sha256,v.raw_path,v.label,v.body,
                    v.metadata,v.collected_at,v.processing_version,v.projection_sha256,'''+','.join(extras)+'''
                    FROM memberships m LEFT JOIN documents d ON d.id=m.document_id
                    LEFT JOIN versions v ON v.id=m.current_version'''+''.join(joins)+'''
                    WHERE m.target_id=? AND m.availability='available' ORDER BY m.document_id''',
                    (project,permission,target_id))
        for row in scoped_rows():
            counts['memberships'] += 1; examine(*row[:17])
            if (not isinstance(row['document_id'], str)
                    or row['current_version'] is not None and not isinstance(row['current_version'], str)):
                flag('membership_identity_invalid', 'membership:' + str(counts['memberships']))
                continue
            key = row['document_id'] + '/' + str(row['current_version'])
            if row['platform'] is None:
                flag('missing_document', key); continue
            if row['version_document'] != row['document_id']:
                flag('missing_or_foreign_current_version', key); continue
            if row['document_availability'] != 'available':
                flag('availability_conflict', key); continue
            allowed[row['project']].add(row['document_id'])
            degree[row['project']][row['document_id']] += 0
            unique = (row['project'], row['document_id'], row['current_version'])
            context = (row['target_id'], row['platform'], row['permission'])
            if all(isinstance(part, str) and part for part in context):
                membership_contexts[unique].add(context)
            else:
                flag('membership_context_invalid', key)
            scoped_versions[(row['project'], row['document_id'])].add(row['current_version'])
            if unique in seen:
                continue
            seen.add(unique); counts['project_current_versions'] += 1
            try:
                if row['document_id'] != identity(row['platform'], row['original_id']):
                    flag('stable_identity_mismatch', key)
            except (TypeError, ValueError):
                flag('stable_identity_invalid', key)
            label, body = row['label'], row['body']
            if (type(row['collected_at']) not in (int, float)
                    or not math.isfinite(row['collected_at']) or row['collected_at'] <= 0):
                flag('collection_time_invalid', key)
            text_valid = isinstance(label, str) and isinstance(body, str)
            binding_valid = all(isinstance(row[field], str) for field in
                                ('raw_sha256', 'raw_path', 'processing_version', 'projection_sha256'))
            if not text_valid:
                flag('version_text_invalid', key)
            if not binding_valid:
                flag('version_binding_invalid', key)
            if not text_valid or not binding_valid:
                continue
            # The collection writer intentionally preserves indentation,
            # tabs and blank lines in bodies. Only NFC and CRLF/CR are canonical.
            if label != normalized_text(label) or body != normalized_body(body):
                flag('normalization_mismatch', key)
            if not body.strip():
                counts['stored_body_empty'] += 1
            metadata_valid = False
            try:
                if not isinstance(row['metadata'], str):
                    raise ValueError('metadata_storage')
                metadata = _json(row['metadata'])
                if not isinstance(metadata, dict):
                    raise ValueError('metadata_shape')
                projection = digest(encoded([label, body, {k: v for k, v in metadata.items() if k != 'source'}]))
                if projection != row['projection_sha256']:
                    flag('projection_hash_mismatch', key)
                expected = identity('version', encoded([row['document_id'], row['raw_sha256'], row['processing_version'], projection]).decode())
                if expected != row['current_version']:
                    flag('version_identity_mismatch', key)
                metadata_valid = projection == row['projection_sha256'] and expected == row['current_version']
                if metadata_valid:
                    declared_fields.add(metadata)
                # Bound a source-identity context to the stored evidence. Never
                # emit names/URLs/authors, or infer that distinct IDs are aliases.
                provenance_fields = ('source', 'source_url', 'url', 'source_name',
                                     'author', 'author_id', 'owner', 'name', 'revision')
                provenance = {name: metadata[name] for name in provenance_fields if name in metadata}
                source_tags[unique] = digest(encoded(provenance)) if provenance else ''
            except (ValueError, TypeError, RecursionError):
                flag('metadata_invalid', key)
            blob_key = (row['raw_sha256'], row['raw_path'])
            reference_valid = (re.fullmatch(r'[0-9a-f]{64}', row['raw_sha256']) is not None
                               and row['raw_path'] == row['raw_sha256'] + '.json')
            if not reference_valid:
                flag('source_reference_invalid', key)
            if verify_sources and reference_valid and blob_key not in checked_blobs:
                checked_blobs.add(blob_key)
                try:
                    data = _blob(store.blobs, row['raw_path'])
                    examine(data)
                    if digest(data) != row['raw_sha256']:
                        raise ValueError('raw_hash')
                    source = _json(data)
                    source_annotations[blob_key] = _annotation_facts(source)
                    source_declarations[blob_key] = {name: source[name] for name in ('kind', 'content_indexed')
                        if isinstance(source, dict) and name in source}
                    original = source.get('localOriginalText') if isinstance(source, dict) else None
                    originals[blob_key] = digest(original.encode()) if isinstance(original, str) and original.strip() else None
                    if isinstance(original, str) and original.strip():
                        projected = project_source_text(original, source)
                        projected_originals[blob_key] = digest(projected['body'].encode())
                        if projected['projection'] is not None:
                            numeric_tails.add(blob_key)
                    counts['source_blobs_verified'] += 1
                    source_verified[blob_key] = True
                except (OSError, ValueError, RecursionError):
                    source_verified[blob_key] = False
                    flag('source_blob_invalid', key)
            cached = tuple(row['quality_text_'+field] for field in cache_fields['text']) if row['quality_text_document_id'] is not None else None
            if cached is not None:
                examine(*cached)
            valid = (cached is not None and isinstance(cached[3], str) and cached[:2] == (digest((label+'\n'+body).encode()), row['raw_sha256'])
                     and cached[2] in ('retained-local-original-text-v1', TEXT_VERSION)
                     and (cached[4] == 'stored_body' and cached[3] == body or cached[4] == 'retained_record.localOriginalText'))
            if cached is not None and not valid:
                flag('retained_text_binding_invalid', key)
            expected_body_hash = (projected_originals if cached is not None and cached[2] == TEXT_VERSION else originals).get(blob_key)
            if valid and verify_sources and cached[4] == 'retained_record.localOriginalText' and expected_body_hash != digest(cached[3].encode()):
                flag('retained_text_source_mismatch', key); valid = False
            text = label + '\n' + (cached[3] if valid else body)
            examine(text)
            effective_body = cached[3] if valid else body
            # A cache stamp alone cannot upgrade a declared derived summary to
            # original body evidence. The verified audit also binds its bytes.
            body_source = ('retained_record.localOriginalText'
                if valid and verify_sources and source_verified.get(blob_key)
                and cached[4] == 'retained_record.localOriginalText'
                and expected_body_hash == digest(effective_body.encode()) else 'stored_body')
            evidence_class = content_evidence_class(metadata, effective_body,
                source=source_declarations.get(blob_key) if verify_sources and source_verified.get(blob_key) else None,
                body_source=body_source) if metadata_valid else None
            if evidence_class in ('metadata_only', 'source_metadata_only', 'unread_body_marker', 'metadata_description', 'reference_only'):
                counts['noncontent_current_versions'] += 1
                counts[evidence_class + '_current_versions'] += 1
            # Inspect the immutable stored projection, independently of titles
            # and disposable search-cache bodies. This is text equality only.
            if body_source == 'stored_body' and effective_body == body:
                stored_class = evidence_class
            else:
                stored_class = content_evidence_class(metadata,body,
                    source=source_declarations.get(blob_key) if verify_sources and source_verified.get(blob_key) else None,
                    body_source='stored_body') if metadata_valid else None
            if (verify_sources and metadata_valid and source_verified.get(blob_key)
                    and stored_class in ('content_candidate', 'reference_only') and projected_originals.get(blob_key)):
                projected_body_entries[(row['project'], projected_originals[blob_key])].add(unique)
            if (verify_sources and metadata_valid and source_verified.get(blob_key)
                    and evidence_class in ('content_candidate', 'reference_only')):
                member_annotations[unique] = source_annotations[blob_key]
            if (metadata_valid and stored_class in ('content_candidate', 'reference_only')
                    and _substantive_field(body)
                    and (not verify_sources or source_verified.get(blob_key) is True)):
                reference_only = stored_class == 'reference_only'
                if not reference_only:
                    body_counts['eligible_current_versions'] += 1
                    body_counts['without_substantive_label'] += not _substantive_field(label, title=True)
                body_counts['reference_only_current_versions'] += reference_only
                entries = reference_entries if reference_only else body_entries
                entries[(row['project'], digest(normalized_body(body).encode()))].add(unique)
            if not text.strip():
                counts['search_text_empty'] += 1
                counts['duplicate_text_ineligible'] += 1
            elif not _substantive_field(label, title=True) or not _substantive_field(effective_body):
                # Missing/placeholder fields cannot ground even a conservative
                # text-match candidate. Do not infer completeness from a title.
                counts['duplicate_text_ineligible'] += 1
            elif not metadata_valid:
                counts['duplicate_text_untrusted'] += 1
            elif verify_sources and source_verified.get(blob_key) is not True:
                counts['duplicate_text_source_unverified'] += 1
            else:
                # Conservative search fingerprint: NFC and line endings only
                # for body; no tab/space folding or date/unit/alias rewriting.
                search_key = digest(encoded([normalized_text(label),
                                             normalized_body(effective_body)]))
                entries = metadata_entries if evidence_class != 'content_candidate' else text_entries
                entries[(row['project'], search_key)].add(unique)
            if reference_valid and (not verify_sources or source_verified.get(blob_key) is True):
                raw_groups[(row['project'], row['raw_sha256'])].add(row['document_id'])
                raw_entries[(row['project'], row['raw_sha256'])].add(unique)
            vector = tuple(row['quality_vector_'+field] for field in cache_fields['vector']) if row['quality_vector_document_id'] is not None else None
            if vector is None:
                counts['current_vector_missing'] += 1
                if text.strip():
                    counts['nonempty_search_text_vector_missing'] += 1
            else:
                counts['current_vectors'] += 1
                examine(*vector)
                if vector[0] != digest(text.encode()):
                    flag('vector_text_binding_invalid', key)
                profile_valid = all(isinstance(v, str) and v for v in vector[1:4])
                if not profile_valid:
                    flag('vector_profile_invalid', key)
                profile = None
                if profile_valid:
                    profile_key = vector[1:4]
                    profile = profile_hashes.get(profile_key)
                    if profile is None:
                        profile = digest(encoded(profile_key))
                        # Request-local reuse of exact validated descriptors;
                        # vectors, dimensions and findings are always checked.
                        if len(profile_hashes) < 64:
                            profile_hashes[profile_key] = profile
                raw = vector[4]
                if not isinstance(raw, bytes) or not 4 <= len(raw) <= 8192 or len(raw) % 4:
                    flag('vector_shape_invalid', key)
                else:
                    values = struct.unpack('<' + 'f' * (len(raw)//4), raw)
                    if profile_valid:
                        profiles[profile][len(values)] += 1
                    # These are at most 2048 float32 values. Their finite norm
                    # cannot overflow float64; hypot checks nonfinite input and
                    # computes the norm in C instead of two Python traversals.
                    norm=math.hypot(*values)
                    if not math.isfinite(norm):
                        flag('vector_nonfinite', key)
                    elif abs(norm - 1) > .001:
                        flag('vector_not_unit', key)
        for row in db.execute('''SELECT p.project,p.permission,r.id,r.target_id,r.source,r.target,r.type,r.evidence,r.version,
                h.relation_id AS history_relation_id,(''' + RELATION_HISTORY_MATCH + ''') AS history_bound
                FROM target_projects p JOIN relations r ON r.target_id=p.target_id
                LEFT JOIN relation_versions h ON h.relation_id=r.id AND h.version=r.version
                WHERE p.permission!='denied' AND p.project IN (''' + marks + ''') AND r.active=1
                ORDER BY p.project,r.id''', projects):
            counts['relations'] += 1; examine(*row)
            key = row['id'] if isinstance(row['id'], str) else 'relation:' + str(counts['relations'])
            if not isinstance(row['id'], str) or not row['id']:
                flag('relation_identity_invalid', key)
            if row['history_relation_id'] is None:
                flag('relation_history_missing', key)
            elif row['history_bound'] != 1:
                flag('relation_history_binding_mismatch', key)
            valid_type = isinstance(row['type'], str) and bool(row['type'].strip())
            if not valid_type:
                flag('relation_type_invalid', key)
            if row['source'] not in allowed[row['project']] or row['target'] not in allowed[row['project']]:
                flag('relation_endpoint_unavailable_in_scope', key)
            else:
                degree[row['project']][row['source']] += 1
                degree[row['project']][row['target']] += 1
                if valid_type and row['source'] != row['target']:
                    # Direction, relation type, source target and project scope
                    # remain part of the key; reciprocal edges are descriptive.
                    directed_relations[(row['project'], row['target_id'], row['type'],
                                        row['source'], row['target'])] += 1
            if not isinstance(row['evidence'], str):
                flag('relation_evidence_invalid', key)
                continue
            if digest(row['evidence'].encode()) != row['version']:
                flag('relation_evidence_hash_mismatch', key)
            try:
                evidence = _json(row['evidence'])
                if evidence is None or evidence == {} or evidence == [] or isinstance(evidence, str) and not evidence.strip():
                    flag('relation_evidence_empty', key)
            except (TypeError, ValueError, RecursionError):
                flag('relation_evidence_invalid', key)
    # One stable document may hold separate current versions under separate
    # targets. Preserve those per-scope revisions as a distinct count, never as
    # evidence of two different entities.
    revisions = sorted((project, document, sorted(versions))
                       for (project, document), versions in scoped_versions.items()
                       if len(versions) > 1)
    revision_projects = Counter(project for project, _, _ in revisions)
    revision_samples, revision_taken = [], Counter()
    for project, document, versions in revisions:
        if revision_taken[project] < sample_cap:
            revision_samples.append({'project': project, 'document_id': document,
                                     'versions': len(versions),
                                     'version_ids': versions[:sample_cap]})
            revision_taken[project] += 1
    revision_report = {'groups': len(revisions),
                       'groups_by_project': dict(sorted(revision_projects.items())),
                       'samples': revision_samples, 'classification': 'scoped_revision_variants'}

    # Candidate types are deliberately nonexclusive. A within-source match can
    # also have counterparts in other targets, permissions or provenances.
    def scoped_matches(entries):
        within, across, contexts = defaultdict(set), defaultdict(set), {}
        for (project, sha), members in entries.items():
            by_context = defaultdict(set)
            for member in members:
                for target_id, platform, permission in membership_contexts[member]:
                    context = (target_id, platform, permission, source_tags.get(member, ''))
                    by_context[context].add(member[1])
            for context, documents in by_context.items():
                if len(documents) > 1:
                    within[(project, sha, context)] = documents
            all_documents = set().union(*by_context.values()) if by_context else set()
            if len(by_context) > 1 and len(all_documents) > 1:
                across[(project, sha)] = all_documents
                contexts[(project, sha)] = set(by_context)
        return within, across, contexts

    within_context, across_context, cross_contexts = scoped_matches(text_entries)
    metadata_within, metadata_across, metadata_contexts = scoped_matches(metadata_entries)
    raw_contexts = {}
    for group, members in raw_entries.items():
        raw_contexts[group] = {(target_id, platform, permission, source_tags.get(member, ''))
                               for member in members
                               for target_id, platform, permission in membership_contexts[member]}

    def summarize_candidates(groups, classification, context_map=None, *, raw=False):
        duplicates = [(key, sorted(ids)) for key, ids in sorted(groups.items()) if len(ids) > 1]
        project_groups = Counter(key[0] for key, _ in duplicates)
        selected, taken = [], Counter()
        for key, ids in duplicates:
            project, sha = key[:2]
            if taken[project] >= sample_cap:
                continue
            contexts = (context_map.get(key, set()) if context_map is not None
                        else {key[2]})
            selected.append({
                'project': project, 'sha256': sha, 'document_ids': ids[:sample_cap],
                'members': len(ids), 'context_count': len(contexts),
                'classification': classification,
                'contexts': [{'target_id': ctx[0], 'platform': ctx[1],
                              'permission': ctx[2], 'provenance_sha256': ctx[3] or None}
                             for ctx in sorted(contexts)[:sample_cap]]})
            taken[project] += 1
        result = {
            'groups': len(duplicates),
            'distinct_document_members': sum(len(ids) for _, ids in duplicates),
            'unique_document_ids': len({doc for _, ids in duplicates for doc in ids}),
            'groups_by_project': dict(sorted(project_groups.items())),
            'samples': selected, 'status': 'review_candidates_not_confirmed_entities',
            'classification': classification}
        if raw:
            result['verified_groups'] = len(duplicates) if verify_sources else 0
            result['hash_only_groups'] = 0 if verify_sources else len(duplicates)
        return result

    candidates = {
        'same_raw_bytes': summarize_candidates(
            raw_groups, ('identical_canonical_record_bytes_verified' if verify_sources
                         else 'identical_canonical_record_hash_only'),
            raw_contexts, raw=True),
        'same_normalized_search_text': summarize_candidates(
            within_context, 'text_match_within_one_target_permission_provenance'),
        'cross_context_search_text': summarize_candidates(
            across_context, 'text_match_across_target_permission_or_provenance',
            cross_contexts)}
    body_matches = {'policy':'stored-body-match-v1',
                    'basis':'current_stored_body_projection_not_original_equivalence',
                    'counts':{name:body_counts[name] for name in
                        ('eligible_current_versions','without_substantive_label','reference_only_current_versions')}}
    for name, entries in (('stored_body',body_entries),('reference_text',reference_entries)):
        contexts = {key:{(target_id,platform,permission,source_tags.get(member,''))
                    for member in members for target_id,platform,permission in membership_contexts[member]}
                    for key,members in entries.items()}
        groups = {key:{member[1] for member in members} for key,members in entries.items()}
        body_matches[name] = summarize_candidates(groups,
            'stored_body_text_match_not_source_equivalence' if name=='stored_body'
            else 'reference_text_match_not_linked_content', contexts)
    metadata_matches = {
        'within_context': summarize_candidates(
            metadata_within, 'metadata_name_or_notice_match_not_content'),
        'cross_context': summarize_candidates(
            metadata_across, 'metadata_name_or_notice_match_not_content', metadata_contexts)}
    for match in metadata_matches.values():
        match['status'] = 'metadata_match_not_content'

    reciprocal = sum(min(quantity, directed_relations.get((project, target_id, rel_type,
                                                             dst, src), 0))
                     for (project, target_id, rel_type, src, dst), quantity in directed_relations.items()
                     if src < dst)
    counts['reciprocal_relation_pairs'] = reciprocal
    mixed_profiles = sorted(profile for profile, dimensions in profiles.items() if len(dimensions) > 1)
    counts['vector_profiles_mixed_dimensions'] = len(mixed_profiles)
    numeric_review = {'mixed_vector_dimensions': {'profiles': len(mixed_profiles),
        'profile_sha256_samples': mixed_profiles[:sample_cap],
        'status': 'review_candidate_not_semantic_outlier'}}
    topology = {}
    for project, values in sorted(degree.items()):
        ordered = sorted(values.values())
        percentile = lambda p: ordered[max(0, math.ceil(len(ordered) * p)-1)] if ordered else 0
        topology[project] = {'documents': len(values), 'isolated': sum(v == 0 for v in ordered),
                             'degree_p50': percentile(.5), 'degree_p95': percentile(.95), 'degree_max': max(ordered, default=0),
                             'highest_degree_samples': [{'document_id': k, 'degree': v} for k, v in sorted(values.items(), key=lambda item: (-item[1], item[0]))[:sample_cap]]}
    counts['distinct_current_documents'] = len({document for _, document, _ in seen})
    counts['scoped_current_documents_with_multiple_versions'] = len(revisions)
    for metric in ('memberships', 'project_current_versions', 'relations', 'bytes_examined',
                   'source_blobs_verified', 'stored_body_empty', 'search_text_empty',
                   'duplicate_text_ineligible', 'duplicate_text_untrusted', 'current_vectors', 'current_vector_missing',
                   'nonempty_search_text_vector_missing', 'reciprocal_relation_pairs',
                   'vector_profiles_mixed_dimensions', 'noncontent_current_versions',
                   'metadata_only_current_versions', 'unread_body_marker_current_versions',
                   'metadata_description_current_versions', 'source_metadata_only_current_versions',
                   'reference_only_current_versions',
                   'duplicate_text_source_unverified'):
        counts.setdefault(metric, 0)
    check()
    annotation_review = _annotation_review(member_annotations, projected_body_entries,
        inspected=verify_sources, sample_cap=sample_cap, check=check)
    return {'schema': 4, 'mode': 'read_only_current_versions', 'projects': projects,
            'source_verification_requested': verify_sources, 'counts': dict(sorted(counts.items())),
            'findings': dict(sorted(findings.items())), 'samples': dict(sorted(samples.items())),
            'duplicate_candidates': candidates, 'metadata_matches': metadata_matches,
            'body_matches':body_matches,
            'source_annotation_review': annotation_review,
            'content_evidence': {'policy': POLICY, 'status': 'source_declared_exclusions_not_semantic_verdict'},
            'scoped_revision_variants': revision_report,
            'numeric_review_candidates': numeric_review,
            'declared_field_normalization': declared_fields.report(),
            'source_text_projection': {'policy': SOURCE_TEXT_POLICY,
                'status': 'verified_retained_records' if verify_sources else 'not_inspected',
                'numeric_tail_candidates': len(numeric_tails),
                'same_projected_body_groups': sum(len(members)>1 for members in projected_body_entries.values()),
                'semantic_validity': 'review_candidates_not_confirmed_duplicates'},
            'coverage': {'status': 'complete', 'scope': 'current_permitted_memberships_only',
                         'projects': len(projects), 'memberships_scanned': counts['memberships'],
                         'relations_scanned': counts['relations'], 'max_rows': max_rows,
                         'max_bytes': max_bytes, 'timeout_seconds': timeout,
                         'sample_cap_per_project': sample_cap,
                         'source_verification': 'retained_canonical_json' if verify_sources else 'hash_references_only'},
            'vector_profiles': {p: dict(sorted(v.items())) for p, v in sorted(profiles.items())},
            'topology_distribution': topology,
            'elapsed_s': time.monotonic()-started,
            'limits': ['Coverage is complete only for permitted current memberships within the requested projects. Budgets/timeouts raise an error; no partial report is returned. Samples are bounded subsets, not exhaustive adjudications.',
                       'Collection database uses one read transaction; retrieval has a separate read snapshot and version/hash bindings are checked.',
                       'Denied, deleted and archived-only versions are excluded. Scope revisions of the same stable document are reported separately.',
                       'Malformed scoped rows are reported; checks requiring their invalid fields are skipped.',
                       'NFC/CRLF/CR equivalence is supported; meaningful body indentation, tabs, spaces, line breaks, dates, units, names and aliases are preserved.',
                       'Duplicate text candidates require substantive title and effective body plus valid version binding; blank fields, explicit placeholders, and potentially truncated 1024-character titles are excluded. Completeness is not semantically inferred.',
                       'Source-declared metadata-only or unread-body records are excluded from content text candidates. Their matching names/notices are reported separately and never prove substantive content, original file equality or entity identity.',
                       'Cross-context candidates include target, permission, platform and hash-only source provenance distinctions; within- and cross-context candidate counts can overlap.',
                       'Matching SHA-256 references without --verify-sources are hash-only evidence; verified raw duplicates concern canonical record JSON bytes only, not full original exports or source-capture manifests.',
                       'Byte counts include fetched text/BLOB fields, retained sources and derived search text; they are work accounting, not unique corpus size or peak memory.',
                       'Identical text or bytes does not establish independent sources, semantic truth, common source/identity or permission to merge.',
                       'Reciprocal directed edges and mixed vector dimensions are descriptive review candidates, not contradictory claims or confirmed semantic outliers.',
                       'Vector format/unit norm does not prove semantic quality, same force/direction or cluster validity.',
                       'Verified retained tag declarations remain semantically unverified. NFC-equivalent repeated values and different declared sets on identical projected retained bodies are review candidates, not incorrect tags, contradictory claims or merge authority. Undeclared/unsupported/over-budget fields are counted separately and do not establish a difference.',
                       'Degree tails and isolation are descriptive statistics; legitimate hubs and separate sources are not labelled semantic anomalies.',
                       'No source/store/index/model/queue edits; no semantic similarity inference or model loading.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-root', type=Path, required=True)
    parser.add_argument('--project', action='append', required=True)
    parser.add_argument('--verify-sources', action='store_true')
    args = parser.parse_args()
    print(json.dumps(audit(args.state_root, args.project, verify_sources=args.verify_sources), ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
