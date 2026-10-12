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

from alden_collection import CollectionStore, MAX_RECORD_BYTES, digest, encoded, identity, normalized_body, normalized_text, RELATION_HISTORY_MATCH
from alden_content_evidence import POLICY, content_evidence_class


def _json(value):
    def reject(value):
        raise ValueError("nonfinite_json")

    def finite_float(value):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError("nonfinite_json")
        return result

    def unique_object(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("duplicate_json_key")
            result[key] = item
        return result

    # A matching byte hash cannot make an ambiguous object authoritative.
    # parse_constant alone also misses finite JSON literals such as 1e400
    # that overflow the parser's float representation, including nested ones.
    return json.loads(value, parse_constant=reject, parse_float=finite_float,
                      object_pairs_hook=unique_object)


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
    allowed, checked_blobs = defaultdict(set), set()
    membership_contexts, source_tags, scoped_versions = defaultdict(set), {}, defaultdict(set)
    seen, profiles, originals, source_verified = set(), defaultdict(Counter), {}, {}
    source_declarations = {}
    degree, directed_relations = defaultdict(Counter), Counter()

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
        for value in values:
            if isinstance(value, str):
                counts['bytes_examined'] += len(value.encode())
            elif isinstance(value, bytes):
                counts['bytes_examined'] += len(value)
        check()

    retrieval = store.root / 'retrieval.sqlite3'
    if any(p.is_symlink() for p in [retrieval, *retrieval.parents]):
        raise ValueError('quality_retrieval_symlink')
    cache_context = closing(sqlite3.connect(retrieval.as_uri() + '?mode=ro', uri=True, timeout=.15)) if retrieval.is_file() else nullcontext(None)
    with store.database() as db, cache_context as cache:
        if cache is not None:
            cache.execute('PRAGMA query_only=ON'); cache.execute('BEGIN')
        cache_tables = {r[0] for r in cache.execute("SELECT name FROM sqlite_master WHERE type='table'")} if cache else set()
        marks = ','.join('?' for _ in projects)
        rows = db.execute('''SELECT p.project,p.permission,m.target_id,m.document_id,m.current_version,
            d.platform,d.original_id,d.availability AS document_availability,
            v.document_id AS version_document,v.raw_sha256,v.raw_path,v.label,v.body,
            v.metadata,v.collected_at,v.processing_version,v.projection_sha256
            FROM target_projects p JOIN memberships m ON m.target_id=p.target_id
            LEFT JOIN documents d ON d.id=m.document_id LEFT JOIN versions v ON v.id=m.current_version
            WHERE p.permission!='denied' AND p.project IN (''' + marks + ''')
            AND m.availability='available' ORDER BY p.project,m.target_id,m.document_id''', projects)
        for row in rows:
            counts['memberships'] += 1; examine(*row)
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
                    source_declarations[blob_key] = {name: source[name] for name in ('kind', 'content_indexed')
                        if isinstance(source, dict) and name in source}
                    original = source.get('localOriginalText') if isinstance(source, dict) else None
                    originals[blob_key] = digest(original.encode()) if isinstance(original, str) and original.strip() else None
                    counts['source_blobs_verified'] += 1
                    source_verified[blob_key] = True
                except (OSError, ValueError, RecursionError):
                    source_verified[blob_key] = False
                    flag('source_blob_invalid', key)
            cached = cache.execute('SELECT base_hash,raw_sha256,extraction,body,body_source FROM texts WHERE document_id=? AND version=?', (row['document_id'], row['current_version'])).fetchone() if 'texts' in cache_tables else None
            if cached is not None:
                examine(*cached)
            valid = (cached is not None and isinstance(cached[3], str) and cached[:3] == (digest((label+'\n'+body).encode()), row['raw_sha256'], 'retained-local-original-text-v1')
                     and (cached[4] == 'stored_body' and cached[3] == body or cached[4] == 'retained_record.localOriginalText'))
            if cached is not None and not valid:
                flag('retained_text_binding_invalid', key)
            if valid and verify_sources and cached[4] == 'retained_record.localOriginalText' and originals.get(blob_key) != digest(cached[3].encode()):
                flag('retained_text_source_mismatch', key); valid = False
            text = label + '\n' + (cached[3] if valid else body)
            examine(text)
            effective_body = cached[3] if valid else body
            # A cache stamp alone cannot upgrade a declared derived summary to
            # original body evidence. The verified audit also binds its bytes.
            body_source = ('retained_record.localOriginalText'
                if valid and verify_sources and source_verified.get(blob_key)
                and cached[4] == 'retained_record.localOriginalText'
                and originals.get(blob_key) == digest(effective_body.encode()) else 'stored_body')
            evidence_class = content_evidence_class(metadata, effective_body,
                source=source_declarations.get(blob_key) if verify_sources and source_verified.get(blob_key) else None,
                body_source=body_source) if metadata_valid else None
            if evidence_class in ('metadata_only', 'source_metadata_only', 'unread_body_marker', 'metadata_description'):
                counts['noncontent_current_versions'] += 1
                counts[evidence_class + '_current_versions'] += 1
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
            vector = cache.execute('SELECT text_hash,model,endpoint,encoding,vector FROM vectors WHERE document_id=? AND version=?', (row['document_id'], row['current_version'])).fetchone() if 'vectors' in cache_tables else None
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
                profile = digest(encoded(vector[1:4])) if profile_valid else None
                raw = vector[4]
                if not isinstance(raw, bytes) or not 4 <= len(raw) <= 8192 or len(raw) % 4:
                    flag('vector_shape_invalid', key)
                else:
                    values = struct.unpack('<' + 'f' * (len(raw)//4), raw)
                    if profile_valid:
                        profiles[profile][len(values)] += 1
                    if any(not math.isfinite(v) for v in values):
                        flag('vector_nonfinite', key)
                    elif abs(math.sqrt(sum(v*v for v in values)) - 1) > .001:
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
                   'duplicate_text_source_unverified'):
        counts.setdefault(metric, 0)
    check()
    return {'schema': 4, 'mode': 'read_only_current_versions', 'projects': projects,
            'source_verification_requested': verify_sources, 'counts': dict(sorted(counts.items())),
            'findings': dict(sorted(findings.items())), 'samples': dict(sorted(samples.items())),
            'duplicate_candidates': candidates, 'metadata_matches': metadata_matches,
            'content_evidence': {'policy': POLICY, 'status': 'source_declared_exclusions_not_semantic_verdict'},
            'scoped_revision_variants': revision_report,
            'numeric_review_candidates': numeric_review,
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
