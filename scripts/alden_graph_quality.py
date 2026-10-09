"""Read-only, current-version collection audit; candidates never authorize merging."""
from collections import Counter, defaultdict
from contextlib import closing, nullcontext
import argparse
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import struct
import time

from alden_collection import CollectionStore, MAX_RECORD_BYTES, digest, encoded, identity, normalized_text


def _json(value):
    def reject(value):
        raise ValueError("nonfinite_json")
    return json.loads(value, parse_constant=reject)


def _blob(folder, name):
    if not isinstance(name, str) or not re.fullmatch(r"[0-9a-f]{64}\.json", name):
        raise ValueError("quality_blob_name")
    if any(p.is_symlink() for p in [folder, *folder.parents]):
        raise ValueError("quality_blob_symlink")
    parent = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
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
    finally:
        os.close(parent)


def audit(root, projects, *, verify_sources=False, max_rows=100000,
          max_bytes=512 * 1024 * 1024, timeout=120, sample_cap=5):
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
    projects = sorted(set(projects))
    counts, findings, samples = Counter(), Counter(), defaultdict(list)
    raw_groups, text_groups, allowed, checked_blobs = defaultdict(set), defaultdict(set), defaultdict(set), set()
    seen, profiles, originals = set(), defaultdict(Counter), {}
    degree = defaultdict(Counter)

    def check():
        if time.monotonic() - started > timeout:
            raise RuntimeError("quality_time_budget")
        if counts['memberships'] + counts['relations'] > max_rows or counts['bytes_examined'] > max_bytes:
            raise RuntimeError("quality_scan_budget")

    def flag(kind, key):
        findings[kind] += 1
        if len(samples[kind]) < sample_cap:
            samples[kind].append(key)

    retrieval = store.root / 'retrieval.sqlite3'
    if any(p.is_symlink() for p in [retrieval, *retrieval.parents]):
        raise ValueError('quality_retrieval_symlink')
    cache_context = closing(sqlite3.connect(retrieval.as_uri() + '?mode=ro', uri=True, timeout=.15)) if retrieval.is_file() else nullcontext(None)
    with store.database() as db, cache_context as cache:
        if cache is not None:
            cache.execute('PRAGMA query_only=ON'); cache.execute('BEGIN')
        cache_tables = {r[0] for r in cache.execute("SELECT name FROM sqlite_master WHERE type='table'")} if cache else set()
        marks = ','.join('?' for _ in projects)
        rows = db.execute('''SELECT p.project,m.target_id,m.document_id,m.current_version,
            d.platform,d.original_id,d.availability AS document_availability,
            v.document_id AS version_document,v.raw_sha256,v.raw_path,v.label,v.body,
            v.metadata,v.collected_at,v.processing_version,v.projection_sha256
            FROM target_projects p JOIN memberships m ON m.target_id=p.target_id
            LEFT JOIN documents d ON d.id=m.document_id LEFT JOIN versions v ON v.id=m.current_version
            WHERE p.permission!='denied' AND p.project IN (''' + marks + ''')
            AND m.availability='available' ORDER BY p.project,m.target_id,m.document_id''', projects)
        for row in rows:
            counts['memberships'] += 1; check()
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
            if unique in seen:
                continue
            seen.add(unique); counts['project_current_versions'] += 1
            try:
                if row['document_id'] != identity(row['platform'], row['original_id']):
                    flag('stable_identity_mismatch', key)
            except (TypeError, ValueError):
                flag('stable_identity_invalid', key)
            label, body = row['label'], row['body']
            counts['bytes_examined'] += len(label.encode()) + len(body.encode()) + len(row['metadata'].encode())
            if label != normalized_text(label) or body != normalized_text(body):
                flag('normalization_mismatch', key)
            if not body.strip():
                counts['stored_body_empty'] += 1
            try:
                metadata = _json(row['metadata'])
                if not isinstance(metadata, dict):
                    raise ValueError('metadata_shape')
                projection = digest(encoded([label, body, {k: v for k, v in metadata.items() if k != 'source'}]))
                if projection != row['projection_sha256']:
                    flag('projection_hash_mismatch', key)
                expected = identity('version', encoded([row['document_id'], row['raw_sha256'], row['processing_version'], projection]).decode())
                if expected != row['current_version']:
                    flag('version_identity_mismatch', key)
            except (ValueError, TypeError):
                flag('metadata_invalid', key)
            if type(row['collected_at']) not in (int, float) or not math.isfinite(row['collected_at']):
                flag('collection_time_invalid', key)
            blob_key = (row['raw_sha256'], row['raw_path'])
            if verify_sources and blob_key not in checked_blobs:
                checked_blobs.add(blob_key)
                try:
                    data = _blob(store.blobs, row['raw_path'])
                    counts['bytes_examined'] += len(data)
                    if digest(data) != row['raw_sha256']:
                        raise ValueError('raw_hash')
                    source = _json(data)
                    original = source.get('localOriginalText') if isinstance(source, dict) else None
                    originals[blob_key] = digest(original.encode()) if isinstance(original, str) and original.strip() else None
                    counts['source_blobs_verified'] += 1
                except (OSError, ValueError, RuntimeError):
                    flag('source_blob_invalid', key)
            cached = cache.execute('SELECT base_hash,raw_sha256,extraction,body,body_source FROM texts WHERE document_id=? AND version=?', (row['document_id'], row['current_version'])).fetchone() if 'texts' in cache_tables else None
            valid = (cached is not None and isinstance(cached[3], str) and cached[:3] == (digest((label+'\n'+body).encode()), row['raw_sha256'], 'retained-local-original-text-v1')
                     and (cached[4] == 'stored_body' and cached[3] == body or cached[4] == 'retained_record.localOriginalText'))
            if cached is not None and not valid:
                flag('retained_text_binding_invalid', key)
            if valid and verify_sources and cached[4] == 'retained_record.localOriginalText' and originals.get(blob_key) != digest(cached[3].encode()):
                flag('retained_text_source_mismatch', key); valid = False
            text = label + '\n' + (cached[3] if valid else body)
            counts['bytes_examined'] += len(text.encode()); check()
            if not text.strip():
                counts['search_text_empty'] += 1
            else:
                text_groups[(row['project'], digest(normalized_text(text).encode()))].add(row['document_id'])
            raw_groups[(row['project'], row['raw_sha256'])].add(row['document_id'])
            vector = cache.execute('SELECT text_hash,model,endpoint,encoding,vector FROM vectors WHERE document_id=? AND version=?', (row['document_id'], row['current_version'])).fetchone() if 'vectors' in cache_tables else None
            if vector is None:
                counts['current_vector_missing'] += 1
                if text.strip():
                    counts['nonempty_search_text_vector_missing'] += 1
            else:
                counts['current_vectors'] += 1
                if vector[0] != digest(text.encode()):
                    flag('vector_text_binding_invalid', key)
                if any(not isinstance(v, str) or not v for v in vector[1:4]):
                    flag('vector_profile_invalid', key)
                profile = digest(encoded(vector[1:4]))
                raw = vector[4]
                if isinstance(raw, bytes):
                    counts['bytes_examined'] += len(raw)
                if not isinstance(raw, bytes) or not 4 <= len(raw) <= 8192 or len(raw) % 4:
                    flag('vector_shape_invalid', key)
                else:
                    values = struct.unpack('<' + 'f' * (len(raw)//4), raw)
                    profiles[profile][len(values)] += 1
                    if any(not math.isfinite(v) for v in values):
                        flag('vector_nonfinite', key)
                    elif abs(math.sqrt(sum(v*v for v in values)) - 1) > .001:
                        flag('vector_not_unit', key)
        for row in db.execute('''SELECT p.project,r.id,r.source,r.target,r.evidence,r.version
                FROM target_projects p JOIN relations r ON r.target_id=p.target_id
                WHERE p.permission!='denied' AND p.project IN (''' + marks + ''') AND r.active=1
                ORDER BY p.project,r.id''', projects):
            counts['relations'] += 1; counts['bytes_examined'] += len(row['evidence'].encode()); check()
            if row['source'] not in allowed[row['project']] or row['target'] not in allowed[row['project']]:
                flag('relation_endpoint_unavailable_in_scope', row['id'])
            else:
                degree[row['project']][row['source']] += 1
                degree[row['project']][row['target']] += 1
            if digest(row['evidence'].encode()) != row['version']:
                flag('relation_evidence_hash_mismatch', row['id'])
            try:
                evidence = _json(row['evidence'])
                if evidence is None or evidence == {} or evidence == [] or isinstance(evidence, str) and not evidence.strip():
                    flag('relation_evidence_empty', row['id'])
            except (TypeError, ValueError):
                flag('relation_evidence_invalid', row['id'])
    candidates = {}
    for name, groups in [('same_raw_bytes', raw_groups), ('same_normalized_search_text', text_groups)]:
        duplicates = [(scope, sha, sorted(ids)) for (scope, sha), ids in sorted(groups.items()) if len(ids) > 1]
        project_groups = Counter(scope for scope, _, _ in duplicates)
        selected, taken = [], Counter()
        for scope, sha, ids in duplicates:
            if taken[scope] < sample_cap:
                selected.append({'project': scope, 'sha256': sha, 'document_ids': ids[:sample_cap], 'members': len(ids)})
                taken[scope] += 1
        candidates[name] = {'groups': len(duplicates), 'distinct_document_members': sum(len(ids) for _, _, ids in duplicates),
                            'unique_document_ids': len(set().union(*(set(ids) for _, _, ids in duplicates))),
                            'groups_by_project': dict(sorted(project_groups.items())),
                            'samples': selected}
    topology = {}
    for project, values in sorted(degree.items()):
        ordered = sorted(values.values())
        percentile = lambda p: ordered[max(0, math.ceil(len(ordered) * p)-1)] if ordered else 0
        topology[project] = {'documents': len(values), 'isolated': sum(v == 0 for v in ordered),
                             'degree_p50': percentile(.5), 'degree_p95': percentile(.95), 'degree_max': max(ordered, default=0),
                             'highest_degree_samples': [{'document_id': k, 'degree': v} for k, v in sorted(values.items(), key=lambda item: (-item[1], item[0]))[:sample_cap]]}
    counts['distinct_current_documents'] = len({document for _, document, _ in seen})
    for metric in ('memberships', 'project_current_versions', 'relations', 'bytes_examined',
                   'source_blobs_verified', 'stored_body_empty', 'search_text_empty',
                   'current_vectors', 'current_vector_missing', 'nonempty_search_text_vector_missing'):
        counts.setdefault(metric, 0)
    return {'schema': 1, 'mode': 'read_only_current_versions', 'projects': projects,
            'source_verification_requested': verify_sources, 'counts': dict(sorted(counts.items())),
            'findings': dict(sorted(findings.items())), 'samples': dict(sorted(samples.items())),
            'duplicate_candidates': candidates, 'vector_profiles': {p: dict(sorted(v.items())) for p, v in sorted(profiles.items())},
            'topology_distribution': topology,
            'elapsed_s': time.monotonic()-started,
            'limits': ['Collection database uses one read transaction; retrieval has a separate read snapshot and version/hash bindings are checked.',
                       'Current permitted memberships only; denied, deleted and archived-only versions are excluded.',
                       'Source verification covers retained canonical record JSON blobs, not full original exports, media bytes or source-capture manifests.',
                       'Identical text or bytes does not establish shared entity, independent sources, semantic truth or permission to merge.',
                       'Vector format/unit norm does not prove semantic quality, same force/direction or cluster validity.',
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
