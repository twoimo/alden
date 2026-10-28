"""Explicit original-file snapshots; no directory crawl, links, or inference.

The manifest enumerates stable file IDs under a separately declared root. A
failed read/parse fails the snapshot before publication; only explicit manifest
removal retracts membership. Original bytes are retained in the capture packet,
deduplicated by exact SHA-256 without merging file identities.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import selectors
import stat
import subprocess
import sys
import tempfile
import time
import unicodedata

from alden_collection import encoded, normalized_body
from alden_chat_files import run_owned_json
from alden_file_content import MAX_FILE_BYTES, MAX_TEXT_BYTES, FileContentUnavailable

MAX_FILES = 64
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_TOTAL_FILE_BYTES = 16 * 1024 * 1024
MAX_CAPTURE_BYTES = 32 * 1024 * 1024
MAX_EXTRACTION_BYTES = 4096
PROCESSING_VERSION = 'declared-original-files-v1'


def _check(cancelled):
    if cancelled():
        raise RuntimeError('collection_cancelled')


def _absolute(path):
    if not isinstance(path, (str, Path)):
        raise ValueError('collection_files_path_invalid')
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('collection_files_path_invalid')
    return path


@contextmanager
def _directory(path):
    path = _absolute(path)
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:]:
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd); fd = nxt
        yield fd
    finally:
        os.close(fd)


def _stamp(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _file_stamp(path):
    path = _absolute(path)
    with _directory(path.parent) as directory:
        info = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError('collection_files_nonregular_source')
        return _stamp(info)


def _read(path, maximum, cancelled, *, admit=None):
    path = _absolute(path)
    with _directory(path.parent) as directory:
        return _read_at(directory, path.name, maximum, cancelled, admit=admit)


def _read_at(directory, name, maximum, cancelled, *, admit=None):
    _check(cancelled)
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    with os.fdopen(fd, 'rb') as source:
        before = os.fstat(source.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ValueError('collection_files_nonregular_source')
        if not 0 <= before.st_size <= maximum:
            raise ValueError('collection_files_byte_budget')
        if admit is not None:
            admit(before.st_size)
            _check(cancelled)
        raw = source.read(before.st_size + 1)
        after = os.fstat(source.fileno())
    current = os.stat(name, dir_fd=directory, follow_symlinks=False)
    _check(cancelled)
    if _stamp(before) != _stamp(after) or _stamp(after) != _stamp(current) or len(raw) != before.st_size:
        raise RuntimeError('collection_files_source_changed')
    return raw, before


def _read_relative(root_fd, relative, maximum, cancelled):
    """Resolve listed descendants through the originally verified root inode."""
    parts = PurePosixPath(relative).parts
    directory = os.dup(root_fd)
    try:
        for part in parts[:-1]:
            _check(cancelled)
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory); directory = nxt
        return _read_at(directory, parts[-1], maximum, cancelled)
    finally:
        os.close(directory)


def _object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('collection_files_manifest_invalid')
        value[key] = item
    return value


def _invalid_constant(_):
    raise ValueError('collection_files_manifest_invalid')


def _manifest(target, path, cancelled):
    _check(cancelled)
    config = target['config']
    if target.get('platform') != 'files' or config.get('adapter') != 'file-set':
        raise ValueError('collection_files_target_invalid')
    root = _absolute(config['root'])
    with _directory(root) as fd:
        info = os.fstat(fd); root_identity = info.st_dev, info.st_ino
    raw, before = _read(path, MAX_MANIFEST_BYTES, cancelled)
    try:
        value = json.loads(raw, object_pairs_hook=_object, parse_constant=_invalid_constant)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError('collection_files_manifest_invalid') from error
    if (not isinstance(value, dict) or set(value) != {'schema', 'files'}
            or type(value['schema']) is not int or value['schema'] != 1
            or not isinstance(value['files'], list) or len(value['files']) > MAX_FILES):
        raise ValueError('collection_files_manifest_invalid')
    ids, paths = set(), set()
    for row in value['files']:
        if not isinstance(row, dict) or not {'id', 'path'} <= set(row) or set(row) - {'id', 'path', 'label'}:
            raise ValueError('collection_files_manifest_invalid')
        if (not isinstance(row['id'], str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', row['id'])
                or not isinstance(row['path'], str) or not 1 <= len(row['path']) <= 4096
                or '\\' in row['path'] or any(ord(c) < 32 for c in row['path'])):
            raise ValueError('collection_files_manifest_invalid')
        relative = PurePosixPath(row['path'])
        if (relative.is_absolute() or '..' in relative.parts or str(relative) != row['path']
                or relative.name in {'', '.'} or row['id'] in ids or row['path'] in paths):
            raise ValueError('collection_files_manifest_invalid')
        if 'label' in row and (not isinstance(row['label'], str) or not row['label'].strip() or len(row['label']) > 1024):
            raise ValueError('collection_files_manifest_invalid')
        ids.add(row['id']); paths.add(row['path'])
    return value, raw, before, root, root_identity


class _Cancellation:
    def __init__(self, callback): self.callback = callback
    def raise_if_cancelled(self): _check(self.callback)


def _validate_parsed(value):
    keys = {'processing_version', 'parser', 'text', 'text_sha256', 'truncated', 'scope', 'segments'}
    if (not isinstance(value, dict) or set(value) != keys or value['processing_version'] != 'document-segments-v1'
            or not isinstance(value['parser'], str) or not 1 <= len(value['parser']) <= 128
            or not isinstance(value['text'], str) or not value['text'].strip()
            or len(value['text'].encode()) > MAX_TEXT_BYTES
            or hashlib.sha256(value['text'].encode()).hexdigest() != value['text_sha256']
            or type(value['truncated']) is not bool or not isinstance(value['scope'], dict)
            or type(value['scope'].get('body_complete')) is not bool
            or not isinstance(value['segments'], list) or not 1 <= len(value['segments']) <= 256):
        raise ValueError('collection_files_parser_invalid')
    total = 0
    for segment in value['segments']:
        if (not isinstance(segment, dict) or set(segment) != {'location', 'text', 'text_sha256'}
                or not isinstance(segment['location'], dict) or not segment['location']
                or not isinstance(segment['text'], str)
                or hashlib.sha256(segment['text'].encode()).hexdigest() != segment['text_sha256']):
            raise ValueError('collection_files_parser_invalid')
        total += len(segment['text'].encode())
    if (total > MAX_TEXT_BYTES or len(encoded(value)) > 256_000
            or value['text'] != '\n'.join(segment['text'] for segment in value['segments'])):
        raise ValueError('collection_files_parser_invalid')
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ValueError('collection_files_parser_invalid') from error
    return value


def body_complete(parsed):
    """Untruncated extraction alone does not establish original-body coverage."""
    return parsed['scope'].get('body_complete') is True and not parsed['truncated']


def file_extraction_coverage(record, *, stored_body):
    """Validated retained coverage; caller binds its source/version authority."""
    invalid = 'collection_file_extraction_invalid'
    if (not isinstance(record, dict) or type(record.get('schema')) is not int or record['schema'] != 1
            or type(record.get('input_bytes')) is not int or not 1 <= record['input_bytes'] <= MAX_FILE_BYTES
            or any(not isinstance(record.get(key), str) or not re.fullmatch(r'[0-9a-f]{64}', record[key])
                   for key in ('input_sha256', 'parser_code_sha256'))
            or not isinstance(stored_body, str)):
        raise ValueError(invalid)
    try:
        parsed = _validate_parsed(record.get('parsed'))
        scope_kind = parsed['scope'].get('kind')
        if (not re.fullmatch(r'[a-z][a-z0-9_.:-]{0,127}', parsed['parser'])
                or not isinstance(scope_kind, str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,63}', scope_kind)
                or parsed['truncated'] and parsed['scope']['body_complete']
                or normalized_body(parsed['text']) != stored_body):
            raise ValueError(invalid)
        complete, partial = body_complete(parsed), parsed['truncated']
        return {'parser': parsed['parser'], 'processing_version': parsed['processing_version'], 'scope_kind': scope_kind,
            'input_sha256': record['input_sha256'], 'input_bytes': record['input_bytes'],
            'extracted_text_sha256': parsed['text_sha256'], 'extracted_text_bytes': len(parsed['text'].encode()),
            'segment_count': len(parsed['segments']), 'body_complete': complete, 'parser_truncated': partial,
            'status': 'partial' if partial else 'complete' if complete else 'unverified'}
    except (TypeError, ValueError) as error:
        raise ValueError(invalid) from error


def source_extraction_packet(record, *, binding, stored_body, detail_body):
    """Bind retained original coverage to this exact detail, without original IO.

    The caller verifies source authority, archive and stored processing version.
    Original-byte retention remains a separate capture assertion.
    """
    invalid = 'collection_file_extraction_invalid'
    if (not isinstance(binding, dict) or set(binding) != {'document_id', 'target_id', 'version', 'raw_sha256', 'detail_body_sha256'}
            or any(not isinstance(binding[key], str) or not re.fullmatch(r'[\x21-\x7e]{1,256}', binding[key])
                   for key in ('document_id', 'target_id', 'version'))
            or any(not isinstance(binding[key], str) or not re.fullmatch(r'[0-9a-f]{64}', binding[key])
                   for key in ('raw_sha256', 'detail_body_sha256'))
            or not isinstance(stored_body, str) or not isinstance(detail_body, str)
            or stored_body[:12000] != detail_body):
        raise ValueError(invalid)
    try:
        if hashlib.sha256(detail_body.encode()).hexdigest() != binding['detail_body_sha256']:
            raise ValueError(invalid)
        packet = {'schema': 1, 'kind': 'declared-original-file-extraction', 'binding': dict(binding),
                  **file_extraction_coverage(record, stored_body=stored_body)}
        if len(encoded(packet)) > MAX_EXTRACTION_BYTES:
            raise ValueError(invalid)
        return packet
    except (TypeError, ValueError) as error:
        raise ValueError(invalid) from error


class SourceBodyError(ValueError):
    """Fixed, non-secret failures shared by retained-original body readers."""
    CODES = frozenset({
        'alden_source_body_unavailable', 'alden_source_body_capture_unavailable',
        'alden_source_body_integrity', 'alden_source_body_format_unsupported',
        'alden_source_body_encoding_unsupported', 'alden_source_body_cursor_invalid',
    })

    def __init__(self, code):
        if not isinstance(code, str) or code not in self.CODES:
            raise ValueError('collection_source_body_error_code_invalid')
        self.code = code
        super().__init__(code)


def original_utf8_material(packet, *, record, record_pointer, original_files,
                           cancelled=lambda: False):
    """Return fresh {data: bytes, text: str} after full original verification.

    No IO, parser, or authority lookup occurs here. The caller verifies the
    capture archive, current membership/version and actual stored-body match.
    Bytes and decoded text retain their exact original Unicode and line endings;
    parser coverage is validated without promoting it to complete coverage.
    """
    if not callable(cancelled):
        raise ValueError('collection_source_body_request_invalid')

    def fail(code='alden_source_body_integrity'):
        raise SourceBodyError(code) from None

    def sha(value):
        return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value) is not None

    def canonical(value):
        try:
            return encoded(value)
        except (TypeError, ValueError, UnicodeError, RecursionError):
            fail()

    def unbase64(value, maximum):
        _check(cancelled)
        if not isinstance(value, str) or len(value) > 4 * ((maximum + 2) // 3):
            fail()
        try:
            raw = base64.b64decode(value, validate=True)
        except (ValueError, UnicodeError):
            fail()
        if len(raw) > maximum or base64.b64encode(raw).decode('ascii') != value:
            fail()
        _check(cancelled)
        return raw

    _check(cancelled)
    if (not isinstance(packet, dict) or type(packet.get('schema')) is not int or packet['schema'] != 1
            or packet.get('kind') != 'declared-original-file-set-v1'
            or not isinstance(record, dict)):
        fail()
    root = record.get('source_root')
    if (not isinstance(root, str) or not 1 <= len(root) <= 4096 or '\x00' in root
            or not PurePosixPath(root).is_absolute() or '..' in PurePosixPath(root).parts
            or str(PurePosixPath(root)) != root or packet.get('root') != root):
        fail()
    files, blobs = packet.get('files'), packet.get('blobs_base64')
    if (not isinstance(files, list) or not 1 <= len(files) <= MAX_FILES
            or not isinstance(blobs, dict) or not 1 <= len(blobs) <= MAX_FILES
            or any(not sha(key) for key in blobs)
            or not isinstance(record_pointer, str)
            or re.fullmatch(r'/files/(0|[1-9][0-9]?)/record', record_pointer) is None):
        fail()
    index = int(record_pointer.split('/')[2])
    if index >= len(files):
        fail()
    selected = files[index]
    if (not isinstance(selected, dict) or set(selected) != {'entry', 'record', 'blob_sha256'}
            or canonical(selected['record']) != canonical(record)):
        fail()

    manifest_raw = unbase64(packet.get('manifest_base64'), MAX_MANIFEST_BYTES)
    if not sha(packet.get('manifest_sha256')) or hashlib.sha256(manifest_raw).hexdigest() != packet['manifest_sha256']:
        fail()
    try:
        manifest = json.loads(manifest_raw, object_pairs_hook=_object, parse_constant=_invalid_constant)
    except (ValueError, UnicodeError, RecursionError):
        fail()
    if (not isinstance(manifest, dict) or set(manifest) != {'schema', 'files'}
            or type(manifest['schema']) is not int or manifest['schema'] != 1
            or not isinstance(manifest['files'], list) or len(manifest['files']) != len(files)):
        fail()
    ids, paths = set(), set()
    for declaration, item in zip(manifest['files'], files):
        _check(cancelled)
        if (not isinstance(declaration, dict) or not {'id', 'path'} <= set(declaration)
                or set(declaration) - {'id', 'path', 'label'}):
            fail()
        file_id, path = declaration['id'], declaration['path']
        if (not isinstance(file_id, str) or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', file_id) is None
                or not isinstance(path, str) or not 1 <= len(path) <= 4096
                or '\\' in path or any(ord(c) < 32 for c in path)):
            fail()
        relative = PurePosixPath(path)
        if (relative.is_absolute() or '..' in relative.parts or str(relative) != path
                or relative.name in {'', '.'} or file_id in ids or path in paths
                or 'label' in declaration and (not isinstance(declaration['label'], str)
                    or not declaration['label'].strip() or len(declaration['label']) > 1024)):
            fail()
        if (not isinstance(item, dict) or set(item) != {'entry', 'record', 'blob_sha256'}
                or not isinstance(item['record'], dict) or not sha(item['blob_sha256'])
                or canonical(item['entry']) != canonical(declaration)):
            fail()
        ids.add(file_id); paths.add(path)
    declaration = manifest['files'][index]
    if record.get('file_id') != declaration['id'] or record.get('path') != declaration['path']:
        fail()
    suffix, parsed = record.get('format'), record.get('parsed')
    if not isinstance(suffix, str) or not isinstance(parsed, dict):
        fail()
    if suffix not in {'.txt', '.md'}:
        fail('alden_source_body_format_unsupported')
    if PurePosixPath(record['path']).suffix.lower() != suffix:
        fail()
    if (not isinstance(parsed.get('processing_version'), str) or not isinstance(parsed.get('parser'), str)
            or not isinstance(parsed.get('scope'), dict) or not isinstance(parsed['scope'].get('kind'), str)
            or not isinstance(parsed.get('text'), str)):
        fail()
    if (parsed['processing_version'] != 'document-segments-v1' or parsed['parser'] != 'text-' + suffix[1:]
            or parsed['scope']['kind'] != 'original-lines'):
        fail('alden_source_body_format_unsupported')
    try:
        coverage = file_extraction_coverage(record, stored_body=normalized_body(parsed['text']))
    except (TypeError, ValueError, UnicodeError, RecursionError):
        fail()
    _check(cancelled)
    if not isinstance(original_files, list) or len(original_files) != len(files):
        fail()
    originals = [item for item in original_files if isinstance(item, dict) and item.get('file_id') == record['file_id']]
    if len(originals) != 1:
        fail()
    original, digest = originals[0], record['input_sha256']
    if (set(original) != {'file_id', 'sha256', 'json_pointer', 'encoding', 'body_complete'}
            or original['sha256'] != digest or selected['blob_sha256'] != digest
            or original['json_pointer'] != '/blobs_base64/' + digest or original['encoding'] != 'base64'
            or type(original['body_complete']) is not bool
            or original['body_complete'] != coverage['body_complete'] or digest not in blobs):
        fail()
    data = unbase64(blobs[digest], MAX_FILE_BYTES)
    if len(data) != record['input_bytes'] or hashlib.sha256(data).hexdigest() != digest:
        fail()
    _check(cancelled)
    try:
        text = data.decode('utf-8')
    except UnicodeError:
        fail('alden_source_body_encoding_unsupported')
    for position, character in enumerate(text):
        if position % 4096 == 0:
            _check(cancelled)
        if unicodedata.category(character) == 'Cc' and character not in '\n\r\t':
            fail('alden_source_body_encoding_unsupported')
    _check(cancelled)
    return {'data': data, 'text': text}


def original_utf8_window(packet, *, record, record_pointer, original_files,
                         offset_bytes=0, max_bytes=16000, cancelled=lambda: False):
    """Return seven byte-range fields from the same verified original material."""
    if (type(offset_bytes) is not int or not 0 <= offset_bytes <= MAX_FILE_BYTES
            or type(max_bytes) is not int or not 4 <= max_bytes <= MAX_TEXT_BYTES
            or not callable(cancelled)):
        raise ValueError('collection_source_body_request_invalid')
    material = original_utf8_material(packet, record=record, record_pointer=record_pointer,
        original_files=original_files, cancelled=cancelled)
    data = material['data']
    total = len(data)
    if offset_bytes > total or offset_bytes < total and data[offset_bytes] & 0xc0 == 0x80:
        raise SourceBodyError('alden_source_body_cursor_invalid') from None
    end = min(offset_bytes + max_bytes, total)
    while end < total and data[end] & 0xc0 == 0x80:
        end -= 1
    window = data[offset_bytes:end]
    result = {'encoding': 'utf-8', 'offset_bytes': offset_bytes, 'end_bytes': end,
              'next_offset_bytes': end if end < total else None, 'total_bytes': total,
              'text': window.decode('utf-8'), 'text_sha256': hashlib.sha256(window).hexdigest()}
    _check(cancelled)
    return result


def parse_file(data, filename, *, cancelled=lambda: False):
    """Reuse the desktop's bounded, cancellable owned process-group controller."""
    _check(cancelled)
    with tempfile.TemporaryDirectory(prefix='alden-file-set-') as folder:
        path = Path(folder).resolve() / 'source.bytes'
        with path.open('xb') as out: out.write(data)
        path.chmod(0o600)
        code, value = run_owned_json([sys.executable, '-E', '-B', '-s', str(Path(__file__).resolve()),
            '--parse', str(path), Path(filename).suffix.lower(), hashlib.sha256(data).hexdigest()],
            _Cancellation(cancelled), timeout=12, max_stdout_bytes=256_000, error_prefix='collection_files_parser')
    if code:
        reason = value.get('unavailable') if isinstance(value, dict) else None
        if isinstance(reason, str) and re.fullmatch(r'document_[a-z0-9_]{1,100}', reason):
            raise FileContentUnavailable(reason)
        raise ValueError('collection_files_parser_unavailable')
    return _validate_parsed(value)


def file_set_snapshot(target, path, *, cancelled=lambda: False):
    started, caller_cancelled = time.monotonic(), cancelled
    def bounded_cancelled():
        if time.monotonic() - started > 120:
            raise ValueError('collection_files_time_budget')
        return caller_cancelled()
    cancelled = bounded_cancelled
    observed = time.time_ns()
    manifest, manifest_raw, manifest_info, root, root_identity = _manifest(target, path, cancelled)
    parser_path = Path(__file__).with_name('alden_document_segments.py')
    parser_sha = hashlib.sha256(parser_path.read_bytes()).hexdigest()
    files, blobs, stamps, peers, total = [], {}, {}, {}, 0
    with _directory(root) as root_fd:
        info = os.fstat(root_fd)
        if (info.st_dev, info.st_ino) != root_identity:
            raise RuntimeError('collection_files_source_changed')
        for entry in manifest['files']:
            _check(cancelled)
            filename = root / entry['path']
            data, info = _read_relative(root_fd, entry['path'], min(MAX_FILE_BYTES, MAX_TOTAL_FILE_BYTES - total), cancelled)
            total += len(data)
            digest = hashlib.sha256(data).hexdigest()
            parsed = _validate_parsed(parse_file(data, filename.name, cancelled=cancelled))
            raw = {'schema': 1, 'file_id': entry['id'], 'path': entry['path'], 'source_root': str(root),
                'input_sha256': digest, 'input_bytes': len(data), 'mtime_ns': info.st_mtime_ns,
                'created_at_seconds': getattr(info, 'st_birthtime', None), 'format': filename.suffix.lower(),
                'parser_code_sha256': parser_sha, 'parsed': parsed}
            files.append({'entry': dict(entry), 'record': raw, 'blob_sha256': digest})
            if digest not in blobs: blobs[digest] = base64.b64encode(data).decode('ascii')
            peers.setdefault(digest, []).append(entry['id'])
            stamps[str(filename)] = _stamp(info)
    capture = encoded({'schema': 1, 'kind': 'declared-original-file-set-v1', 'root': str(root),
        'manifest_sha256': hashlib.sha256(manifest_raw).hexdigest(),
        'manifest_base64': base64.b64encode(manifest_raw).decode('ascii'), 'files': files, 'blobs_base64': blobs,
        'exact_byte_groups': {key: sorted(value) for key, value in peers.items()}})
    if len(capture) > MAX_CAPTURE_BYTES: raise ValueError('collection_files_capture_budget')
    revision = hashlib.sha256(capture).hexdigest()
    source = {'path': str(path), 'root': str(root), 'sha256': revision, 'mtime_ns': observed,
        'observed_at': observed / 1_000_000_000, 'kind': 'declared-original-file-set-v1',
        'file_count': len(files), 'unique_byte_blobs': len(blobs), 'input_bytes': total,
        'manifest_complete': True, 'body_complete': all(body_complete(x['record']['parsed']) for x in files),
        'capture_encoding': 'base64 originals plus structured text; no model inference'}
    records = [{'platform': 'files', 'original_id': encoded([target['original_id'], x['entry']['id']]).decode(),
        'kind': 'source-file', 'label': x['entry'].get('label', Path(x['entry']['path']).name),
        'text': x['record']['parsed']['text'], 'source_file_id': x['entry']['id'],
        'source_path': str(root / x['entry']['path']), 'source_file_sha256': x['blob_sha256'],
        'coverage': 'bounded original-file text; exact locations in raw.parsed.segments; see truncated/scope',
        'raw': x['record'], 'source': source} for x in files]

    def check_current():
        _check(cancelled)
        with _directory(root) as fd:
            info = os.fstat(fd)
            if (info.st_dev, info.st_ino) != root_identity: raise RuntimeError('collection_files_source_changed')
        if _file_stamp(path) != _stamp(manifest_info) or hashlib.sha256(parser_path.read_bytes()).hexdigest() != parser_sha:
            raise RuntimeError('collection_files_source_changed')
        for filename, stamp in stamps.items():
            _check(cancelled)
            if _file_stamp(filename) != stamp: raise RuntimeError('collection_files_source_changed')
    check_current()
    return records, [], source, capture, check_current


def file_set_signature(target):
    """Bounded stat observation hint; no document parsing or content inference."""
    path = _absolute(target['config']['path'])
    manifest, raw, info, root, root_identity = _manifest(target, path, lambda: False)
    stamps = [[entry['id'], entry['path'], _file_stamp(root / entry['path'])] for entry in manifest['files']]
    if _file_stamp(path) != _stamp(info): raise RuntimeError('collection_files_source_changed')
    return hashlib.sha256(encoded([str(path), hashlib.sha256(raw).hexdigest(), str(root), root_identity, stamps])).hexdigest()


def _native(command, *, timeout, stdout_cap, stderr_cap):
    child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    deadline = time.monotonic() + timeout
    output = {'out': bytearray(), 'err': bytearray()}
    try:
        with selectors.DefaultSelector() as selector:
            for name, pipe in [('out', child.stdout), ('err', child.stderr)]:
                os.set_blocking(pipe.fileno(), False); selector.register(pipe, selectors.EVENT_READ, name)
            while selector.get_map():
                if time.monotonic() >= deadline: raise ValueError('collection_files_native_timeout')
                for key, _ in selector.select(.05):
                    chunk = os.read(key.fd, 65536)
                    if not chunk: selector.unregister(key.fileobj); continue
                    output[key.data].extend(chunk)
                    if len(output[key.data]) > (stdout_cap if key.data == 'out' else stderr_cap):
                        raise ValueError('collection_files_native_output_budget')
        code = child.wait(timeout=max(.01, deadline - time.monotonic()))
        return code, bytes(output['out']), bytes(output['err'])
    finally:
        if child.poll() is None: child.kill()
        child.wait(timeout=1); child.stdout.close(); child.stderr.close()


def _main():
    try:
        if len(sys.argv) != 5 or sys.argv[1] != '--parse' or not re.fullmatch(r'[0-9a-f]{64}', sys.argv[4]):
            raise ValueError('collection_files_parser_invalid')
        path = Path(sys.argv[2])
        info = path.stat(follow_symlinks=False)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1:
            raise ValueError('collection_files_parser_invalid')
        data, _ = _read(path, MAX_FILE_BYTES, lambda: False)
        if hashlib.sha256(data).hexdigest() != sys.argv[4]: raise ValueError('collection_files_parser_invalid')
        from alden_document_segments import parse_document_segments
        print(json.dumps(_validate_parsed(parse_document_segments(data, 'source' + sys.argv[3], run_process=_native)), ensure_ascii=False))
        return 0
    except (ValueError, OSError, subprocess.TimeoutExpired) as error:
        reason = str(error) if re.fullmatch(r'document_[a-z0-9_]{1,100}', str(error)) else 'collection_files_parser_unavailable'
        print(json.dumps({'unavailable': reason})); return 2


if __name__ == '__main__':
    raise SystemExit(_main())
