"""Bounded local stdio reads from a pinned, task-owned derived GBrain store.

This transport establishes no original-source permission or entity identity.
Callers must bind returned graph edges to a current authorized Alden snapshot.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import select
import signal
import stat
import subprocess
import time


VERSION = '0.60.451.0'
SOURCE_COMMIT = '33d73cb54c14e2e988272764ddff59a5ead10915'
ASSET = 'gbrain-darwin-arm64'
ASSET_BYTES = 187472354
ASSET_SHA256 = '7e5ea749d4fbd7c45abd96b40fc893a0b9dcba3ee8202f7f68e68ae8949277a2'
PROTOCOL = '2025-06-18'
MAX_FRAME = 8 * 1024 * 1024
_SLUG = re.compile(r'[a-z0-9][a-z0-9/_-]{0,255}\Z')
_NAME = re.compile(r'[a-z0-9][a-z0-9_-]{0,127}\Z')


class GbrainError(RuntimeError):
    pass


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError('duplicate key')
        result[key] = value
    return result


def _finite_float(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError('nonfinite')
    return result


def _json(raw):
    try:
        return json.loads(raw, object_pairs_hook=_pairs,
                          parse_float=_finite_float,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite')))
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise GbrainError('gbrain_invalid_json') from None


def _path(value):
    if not isinstance(value, (str, Path)):
        raise GbrainError('gbrain_path_invalid')
    path = Path(value)
    if not path.is_absolute() or '..' in path.parts or any(p.is_symlink() for p in (path, *path.parents)):
        raise GbrainError('gbrain_path_invalid')
    return path


def validate_binary(receipt_path, owned_root, check=lambda: None):
    """Verify the private publisher pin before even initializing a new store."""
    root, receipt_path = map(_path, (owned_root, receipt_path))
    if not root.is_dir() or root.stat().st_uid != os.getuid() or stat.S_IMODE(root.stat().st_mode) & 0o077:
        raise GbrainError('gbrain_private_root_required')
    if not receipt_path.is_relative_to(root):
        raise GbrainError('gbrain_root_mismatch')
    if not receipt_path.is_file() or receipt_path.stat().st_size > 65536:
        raise GbrainError('gbrain_receipt_budget')
    receipt = _json(receipt_path.read_bytes())
    if not isinstance(receipt, dict) or any(receipt.get(k) != v for k, v in {
        'repository': 'garrytan/gbrain', 'source_commit': SOURCE_COMMIT,
        'tag': 'v' + VERSION, 'asset_name': ASSET, 'bytes': ASSET_BYTES,
        'sha256': ASSET_SHA256, 'github_asset_digest_matches': True,
    }.items()):
        raise GbrainError('gbrain_runtime_pin_mismatch')
    binary = _path(receipt.get('runtime_path', ''))
    if not binary.is_relative_to(root) or not binary.is_file() or binary.stat().st_size != ASSET_BYTES:
        raise GbrainError('gbrain_runtime_path_mismatch')
    def fingerprint():
        value = binary.stat()
        return value.st_dev, value.st_ino, value.st_size, value.st_mode, value.st_mtime_ns, value.st_ctime_ns
    before = fingerprint()
    digest = hashlib.sha256()
    with binary.open('rb') as stream:
        while data := stream.read(1024 * 1024):
            check()
            digest.update(data)
    if digest.hexdigest() != ASSET_SHA256 or fingerprint() != before:
        raise GbrainError('gbrain_runtime_bytes_changed')
    return binary, root


def validate_runtime(receipt_path, owned_root, home, check=lambda: None):
    """Read only a private owned root; never use ambient GBrain configuration."""
    binary, root = validate_binary(receipt_path, owned_root, check)
    home = _path(home)
    if not home.is_relative_to(root):
        raise GbrainError('gbrain_root_mismatch')
    config_path = _path(home / '.gbrain/config.json')
    if not config_path.is_file() or config_path.stat().st_size > 65536:
        raise GbrainError('gbrain_config_unavailable')
    config = _json(config_path.read_bytes())
    if (not isinstance(config, dict) or config.get('engine') != 'pglite'
            or set(config) - {'engine', 'database_path', 'embedding_disabled', 'schema_pack',
                              'mcp', 'self_upgrade', 'protocol_installed_at'}
            or config.get('embedding_disabled') is not True
            or any(config.get(k) for k in ('database_url', 'remote_mcp', 'openai_api_key', 'chat_model'))
            or not isinstance(config.get('self_upgrade', {}), dict)
            or config.get('self_upgrade', {}).get('mode') == 'auto'):
        raise GbrainError('gbrain_local_keyless_store_required')
    database = _path(config.get('database_path', ''))
    if not database.is_relative_to(root) or not database.is_dir():
        raise GbrainError('gbrain_database_root_mismatch')
    check()
    return binary, root, home


class _Stdio:
    def __init__(self, args, *, cwd, env, check, timeout, pass_fds=()):
        self.check, self.timeout = check, timeout
        self.buffer, self.sequence, self.stderr_bytes = b'', 0, 0
        self.stderr_open = True
        self.stderr_tail, self.graph_truncated = b'', False
        self.proc = subprocess.Popen(args, cwd=cwd, env=env, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     start_new_session=True, bufsize=0, pass_fds=pass_fds)
        os.set_blocking(self.proc.stdin.fileno(), False)

    def send(self, message, deadline=None):
        self.check()
        try:
            raw = json.dumps(message, ensure_ascii=False, allow_nan=False).encode() + b'\n'
        except (ValueError, UnicodeError, RecursionError):
            raise GbrainError('gbrain_request_invalid') from None
        if len(raw) > MAX_FRAME:
            raise GbrainError('gbrain_request_budget')
        deadline = time.monotonic() + self.timeout if deadline is None else deadline
        offset = 0
        while offset < len(raw):
            self.check()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise GbrainError('gbrain_deadline')
            _, ready, _ = select.select([], [self.proc.stdin], [], min(.05, remaining))
            if ready:
                try:
                    offset += os.write(self.proc.stdin.fileno(), raw[offset:offset + 65536])
                except BlockingIOError:
                    continue
                except OSError:
                    raise GbrainError('gbrain_stdio_closed') from None

    def receive(self, deadline):
        while b'\n' not in self.buffer:
            self.check()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise GbrainError('gbrain_deadline')
            streams = [self.proc.stdout] + ([self.proc.stderr] if self.stderr_open else [])
            ready, _, _ = select.select(streams, [], [], min(.05, remaining))
            for stream in ready:
                data = os.read(stream.fileno(), 65536)
                if stream is self.proc.stderr:
                    if not data:
                        self.stderr_open = False
                    self.stderr_bytes += len(data)
                    combined = self.stderr_tail + data
                    self.graph_truncated |= b'traverse_graph output truncated' in combined
                    self.stderr_tail = combined[-128:]
                    if self.stderr_bytes > MAX_FRAME:
                        raise GbrainError('gbrain_stderr_budget')
                elif not data:
                    raise GbrainError('gbrain_stdio_closed')
                else:
                    self.buffer += data
                    if len(self.buffer) > MAX_FRAME:
                        raise GbrainError('gbrain_response_budget')
        line, self.buffer = self.buffer.split(b'\n', 1)
        value = _json(line)
        if not isinstance(value, dict) or value.get('jsonrpc') != '2.0':
            raise GbrainError('gbrain_protocol_invalid')
        return value

    def request(self, method, params):
        self.sequence += 1
        identity = self.sequence
        deadline = time.monotonic() + self.timeout
        self.send({'jsonrpc': '2.0', 'id': identity, 'method': method, 'params': params}, deadline)
        for _ in range(1024):
            response = self.receive(deadline)
            if type(response.get('id')) is int and response['id'] == identity:
                if 'error' in response or not isinstance(response.get('result'), dict):
                    raise GbrainError('gbrain_rpc_error')
                return response['result']
            if ('id' in response or not isinstance(response.get('method'), str)
                    or not response['method'].startswith('notifications/')):
                raise GbrainError('gbrain_unexpected_message')
        raise GbrainError('gbrain_notification_budget')

    def close(self):
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            for sig in (signal.SIGTERM, signal.SIGKILL):
                if self.proc.poll() is not None:
                    break
                try:
                    if os.getpgid(self.proc.pid) == self.proc.pid:
                        os.killpg(self.proc.pid, sig)
                    self.proc.wait(timeout=2)
                except (ProcessLookupError, subprocess.TimeoutExpired):
                    continue
        finally:
            for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
                stream.close()
        return self.proc.poll()


class GbrainReadClient:
    def __init__(self, receipt_path, *, owned_root, home, source_id='default',
                 check=lambda: None, timeout=30, lease_fd=None):
        if (not isinstance(source_id, str) or not _NAME.fullmatch(source_id)
                or type(timeout) not in (int, float) or not 0 < timeout <= 60):
            raise GbrainError('gbrain_read_options_invalid')
        self.check, self.timeout, self.source_id = check, timeout, source_id
        self.lease_fd = lease_fd
        self.binary, self.root, self.home = validate_runtime(receipt_path, owned_root, home, check)
        self.transport = None
        self.notice_codes = set()

    def __enter__(self):
        self.check()
        env = {k: os.environ[k] for k in ('PATH', 'LANG', 'LC_ALL', 'TZ', 'HOME', 'TMPDIR') if k in os.environ}
        env.update(GBRAIN_HOME=str(self.home), GBRAIN_AUDIT_DIR=str(self.home.parent / 'audit'),
                   GBRAIN_SOURCE=self.source_id, GBRAIN_SWEEP='0', GBRAIN_SERVE_SYNC_IPC='0')
        args = [str(self.binary), 'serve', '--surface', 'full', '--access', 'read-only',
                '--source-guard', '--stdio-idle-timeout', '30', '--fail-fast']
        self.transport = _Stdio(args, cwd=self.root, env=env, check=self.check, timeout=self.timeout,
                                pass_fds=() if self.lease_fd is None else (self.lease_fd,))
        try:
            result = self.transport.request('initialize', {'protocolVersion': PROTOCOL,
                'capabilities': {}, 'clientInfo': {'name': 'alden-gbrain', 'version': '1'}})
            if result.get('protocolVersion') != PROTOCOL or result.get('serverInfo') != {'name': 'gbrain', 'version': VERSION}:
                raise GbrainError('gbrain_peer_mismatch')
            self.transport.send({'jsonrpc': '2.0', 'method': 'notifications/initialized'})
            tools = self.transport.request('tools/list', {})
            listing = tools.get('tools')
            if (not isinstance(listing, list) or len(listing) > 512
                    or any(not isinstance(tool, dict) or not isinstance(tool.get('name'), str) for tool in listing)):
                raise GbrainError('gbrain_read_surface_invalid')
            names = {tool['name'] for tool in listing}
            if (tools.get('nextCursor') or 'traverse_graph' not in names
                    or names & {'put_page', 'put_pages', 'add_link', 'delete_page'}):
                raise GbrainError('gbrain_read_surface_invalid')
        except BaseException:
            self.transport.close()
            raise
        return self

    def graph(self, slug, *, depth=2, direction='both', link_type=None):
        if (not isinstance(slug, str) or not _SLUG.fullmatch(slug)
                or type(depth) is not int or not 1 <= depth <= 3
                or direction not in ('in', 'out', 'both')
                or link_type is not None and (not isinstance(link_type, str) or not _NAME.fullmatch(link_type))):
            raise GbrainError('gbrain_graph_options_invalid')
        if self.transport is None:
            raise GbrainError('gbrain_client_not_open')
        args = {'slug': slug, 'source_id': self.source_id, 'depth': depth, 'direction': direction}
        if link_type is not None:
            args['link_type'] = link_type
        self.transport.graph_truncated = False
        result = self.transport.request('tools/call', {'name': 'traverse_graph', 'arguments': args})
        content = result.get('content')
        if not isinstance(content, list) or any(not isinstance(part, dict) for part in content):
            raise GbrainError('gbrain_graph_unavailable')
        text = [part.get('text') for part in content if part.get('type') == 'text']
        meta = result.get('_meta', {})
        notices = meta.get('gbrain_notices', []) if isinstance(meta, dict) else None
        if (result.get('isError') or not isinstance(notices, list) or len(notices) > 16
                or len(text) != len(content) or len(text) != 1 + len(notices)
                or any(not isinstance(part, str) for part in text)):
            raise GbrainError('gbrain_graph_unavailable')
        # Upstream's first block is data; following blocks mirror structured
        # notices. The pinned publisher's features_auto_fix is coaching, not
        # degraded graph data. Record it, never execute its suggested fixes
        # (which can include paid/provider operations). Unknown notices fail.
        reviewed_notices = {'first_run_decisions': 'ask', 'features_auto_fix': 'coaching'}
        for notice, block in zip(notices, text[1:]):
            if not isinstance(notice, dict):
                raise GbrainError('gbrain_graph_notice_unreviewed')
            code, kind = notice.get('code'), notice.get('kind')
            if (not isinstance(code, str) or code not in reviewed_notices or reviewed_notices[code] != kind
                    or not block.startswith('[gbrain notice ' + code + ' kind=' + kind + ']\n')):
                raise GbrainError('gbrain_graph_notice_unreviewed')
            self.notice_codes.add(code)
        data = _json(text[0])
        if not isinstance(data, list) or len(data) > 8192:
            raise GbrainError('gbrain_graph_shape_invalid')
        if self.transport.graph_truncated or len(data) >= 5000:
            raise GbrainError('gbrain_graph_truncated')
        self.check()
        return data

    def __exit__(self, kind, value, traceback):
        code = self.transport.close()
        self.transport = None
        if kind is None and code != 0:
            raise GbrainError('gbrain_unclean_shutdown')
