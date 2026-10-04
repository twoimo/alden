#!/usr/bin/env python3
"""Explicit offline artifact verification; never loads or changes a model."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import time
from pathlib import Path

MAX_FILES = 256
MAX_FILE_BYTES = 512 * 1024**3
CHUNK_BYTES = 4 * 1024**2
MAX_MANIFEST_BYTES = 1024**2


def load_manifest(path: Path) -> dict:
    with path.open('rb') as stream:
        raw = stream.read(MAX_MANIFEST_BYTES + 1)
    if len(raw) > MAX_MANIFEST_BYTES:
        raise ValueError('manifest_too_large')
    manifest = json.loads(raw)
    if not isinstance(manifest, dict) or manifest.get('schema_version') != 1:
        raise ValueError('manifest_schema_invalid')
    if not re.fullmatch(r'[0-9a-f]{40}', str(manifest.get('revision', ''))):
        raise ValueError('revision_invalid')
    files = manifest.get('files')
    if not isinstance(files, list) or not 1 <= len(files) <= MAX_FILES:
        raise ValueError('file_count_invalid')
    seen = set()
    for row in files:
        if not isinstance(row, dict):
            raise ValueError('file_record_invalid')
        name, size = row.get('path'), row.get('bytes')
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,199}', name) or name in seen:
            raise ValueError('file_path_invalid')
        if isinstance(size, bool) or not isinstance(size, int) or not 0 <= size <= MAX_FILE_BYTES:
            raise ValueError('file_size_invalid')
        algorithm = row.get('algorithm')
        length = 64 if algorithm == 'sha256' else 40 if algorithm == 'git_blob_sha1' else 0
        if not length or not re.fullmatch('[0-9a-f]{' + str(length) + '}', str(row.get('digest', ''))):
            raise ValueError('file_digest_invalid')
        seen.add(name)
    return manifest


def fingerprint(info: os.stat_result) -> tuple:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_mode)


def verify_file(directory: int, row: dict) -> dict:
    result = {'path': row['path'], 'expected_bytes': row['bytes'], 'algorithm': row['algorithm'], 'match': False}
    descriptor = None
    try:
        descriptor = os.open(row['path'], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            result['reason'] = 'file_not_regular'
            return result
        result['actual_bytes'] = before.st_size
        if before.st_size != row['bytes']:
            result['reason'] = 'file_size_mismatch'
            return result
        digest = hashlib.sha256() if row['algorithm'] == 'sha256' else hashlib.sha1()
        if row['algorithm'] == 'git_blob_sha1':
            digest.update(f'blob {before.st_size}\0'.encode('ascii'))
        read = 0
        while read <= before.st_size:
            chunk = os.read(descriptor, min(CHUNK_BYTES, before.st_size - read + 1))
            if not chunk:
                break
            digest.update(chunk)
            read += len(chunk)
        after = os.fstat(descriptor)
        current = os.stat(row['path'], dir_fd=directory, follow_symlinks=False)
        if read != before.st_size or fingerprint(before) != fingerprint(after) or fingerprint(before) != fingerprint(current):
            result['reason'] = 'file_changed_during_check'
            return result
        result['digest'] = digest.hexdigest()
        result['match'] = result['digest'] == row['digest']
        result['reason'] = 'ok' if result['match'] else 'file_hash_mismatch'
    except OSError:
        result['reason'] = 'file_unavailable'
    finally:
        if descriptor is not None:
            os.close(descriptor)
    return result


def verify_model(model_dir: Path, manifest: dict) -> dict:
    started = time.monotonic()
    directory = os.open(model_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        before = os.fstat(directory)
        rows = [verify_file(directory, row) for row in manifest['files']]
        current = model_dir.stat(follow_symlinks=False)
        same_directory = (before.st_dev, before.st_ino) == (current.st_dev, current.st_ino)
    finally:
        os.close(directory)
    return {
        'schema_version': 1, 'repository': manifest.get('repository'), 'revision': manifest['revision'],
        'scope': manifest.get('scope'), 'ok': same_directory and all(row['match'] for row in rows),
        'directory_identity_unchanged': same_directory, 'checked_files': len(rows),
        'matched_files': sum(row['match'] for row in rows), 'total_expected_bytes': sum(row['bytes'] for row in manifest['files']),
        'checked_at_epoch': int(time.time()), 'elapsed_seconds': time.monotonic() - started, 'rows': rows,
        'network_requests': 0, 'model_loads': 0, 'model_file_writes': 0,
        'runtime_inference_or_residency_verified': False,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-dir', required=True, type=Path)
    parser.add_argument('--manifest', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = verify_model(args.model_dir, load_manifest(args.manifest))
    except (OSError, ValueError, TypeError) as error:
        result = {'ok': False, 'reason': str(error) if isinstance(error, ValueError) else 'verification_input_unavailable'}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
