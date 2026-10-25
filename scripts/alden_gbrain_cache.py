"""Managed, source-bound GBrain cache generations; never an original-data writer.

The fixed bounded graph is rebuilt in a fresh private generation. A changed
source retires the old generation before replacement; it cannot be rolled back
to a withdrawn version. This is not a full-corpus incremental importer.
"""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import select
import shutil
import signal
import stat
import subprocess
import tempfile
import time
import uuid

from alden_gbrain_bridge import GbrainGraphReader
from alden_gbrain_client import (ASSET_SHA256, GbrainError, GbrainReadClient, _json, _path,
                                 validate_binary, validate_runtime)
from alden_gbrain_projection import GbrainProjection

KIND = 'alden-gbrain-cache-v1'
MANIFEST_BYTES = 32 * 1024 * 1024
_GEN = re.compile(r'g[0-9a-f]{32}\Z')
_HEX = re.compile(r'[0-9a-f]{64}\Z')


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _sync(directory):
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write(path, value):
    _path(path)
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
    fd, temporary = tempfile.mkstemp(prefix='.cache-write-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as output:
            output.write(raw); output.flush(); os.fsync(output.fileno())
        os.replace(temporary, path)
        _sync(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _bytes(path, maximum=65536):
    path = _path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
            raise GbrainError('gbrain_cache_record_invalid')
        raw = source.read(maximum + 1)
    if len(raw) > maximum:
        raise GbrainError('gbrain_cache_record_invalid')
    return raw


def _read(path, maximum=65536):
    return _json(_bytes(path, maximum))


def _projects(projects):
    if (not isinstance(projects, list) or not 1 <= len(projects) <= 16
            or any(not isinstance(p, str) or not p or len(p) > 128 for p in projects)
            or len(set(projects)) != len(projects)):
        raise GbrainError('gbrain_project_scope_invalid')
    return sorted(projects)


class _UnreapedWorker(GbrainError):
    pass


class ManagedGbrainCache:
    KIND = KIND

    def __init__(self, root, *, collection_root, projects):
        self.root = _path(root)
        info = self.root.stat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise GbrainError('gbrain_private_root_required')
        self.root_identity = (info.st_dev, info.st_ino)
        self.owner = _read(self.root / 'owner.json')
        expected = {'kind', 'owner', 'collection_root_sha256', 'projects'}
        if (not isinstance(self.owner, dict) or set(self.owner) != expected or self.owner['kind'] != self.KIND
                or not isinstance(self.owner['owner'], str) or not re.fullmatch('[0-9a-f]{32}', self.owner['owner'])
                or self.owner['collection_root_sha256'] != _sha(str(_path(collection_root)).encode())
                or self.owner['projects'] != _projects(projects)):
            raise GbrainError('gbrain_cache_owner_mismatch')
        self.generations = _path(self.root / 'generations')
        if not self.generations.is_dir():
            raise GbrainError('gbrain_cache_record_invalid')

    @classmethod
    def create(cls, root, *, collection_root, projects, runtime_receipt, runtime_root, runtime_home, check=lambda: None):
        root = _path(root)
        collection_root, projects = _path(collection_root), _projects(projects)
        if root.exists() or not root.parent.is_dir():
            raise GbrainError('gbrain_cache_create_requires_new_root')
        binary, _, _ = validate_runtime(runtime_receipt, runtime_root, runtime_home, check)
        # mkdir claims an absent destination without replacing even an empty
        # directory another creator may have made since the existence check.
        root.mkdir(mode=0o700)
        identity = (root.stat().st_dev, root.stat().st_ino)
        fd = os.open(root / 'lease.lock', os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        complete = False
        try:
            (root / 'generations').mkdir(mode=0o700)
            (root / 'runtime').mkdir(mode=0o700)
            shutil.copyfile(binary, root / 'runtime/gbrain')
            (root / 'runtime/gbrain').chmod(0o700)
            if _sha((root / 'runtime/gbrain').read_bytes()) != ASSET_SHA256:
                raise GbrainError('gbrain_runtime_bytes_changed')
            receipt = _read(Path(runtime_receipt))
            receipt['runtime_path'] = str(root / 'runtime/gbrain')
            _write(root / 'runtime-receipt.json', receipt)
            check()
            # owner.json is the bootstrap commit record; partial roots cannot
            # construct a manager or reader. The creator holds the lease too.
            _write(root / 'owner.json', {'kind': cls.KIND, 'owner': uuid.uuid4().hex,
                'collection_root_sha256': _sha(str(collection_root).encode()), 'projects': projects})
            _sync(root.parent)
            complete = True
        finally:
            try:
                if not complete and root.exists():
                    if root.is_symlink() or (root.stat().st_dev, root.stat().st_ino) != identity:
                        raise GbrainError('gbrain_cache_owner_mismatch')
                    shutil.rmtree(root)
            finally:
                os.close(fd)
        return cls(root, collection_root=collection_root, projects=projects)

    @contextmanager
    def lease(self, *, exclusive, check=lambda: None):
        check()
        root_info = _path(self.root).stat()
        if ((root_info.st_dev, root_info.st_ino) != self.root_identity
                or root_info.st_uid != os.getuid() or stat.S_IMODE(root_info.st_mode) != 0o700):
            raise GbrainError('gbrain_cache_owner_mismatch')
        if _read(self.root / 'owner.json') != self.owner:
            raise GbrainError('gbrain_cache_owner_mismatch')
        fd = os.open(_path(self.root / 'lease.lock'), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
                raise GbrainError('gbrain_cache_lease_invalid')
            until = time.monotonic() + 5
            while True:
                check()
                try:
                    fcntl.flock(fd, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= until:
                        raise GbrainError('gbrain_cache_busy')
                    time.sleep(.025)
            if _read(self.root / 'owner.json') != self.owner:
                raise GbrainError('gbrain_cache_owner_mismatch')
            lock_info = _path(self.root / 'lease.lock').stat()
            root_info = self.root.stat()
            if ((lock_info.st_dev, lock_info.st_ino) != (info.st_dev, info.st_ino)
                    or (root_info.st_dev, root_info.st_ino) != self.root_identity
                    or root_info.st_uid != os.getuid() or stat.S_IMODE(root_info.st_mode) != 0o700):
                raise GbrainError('gbrain_cache_lease_invalid')
            yield fd
        finally:
            # No LOCK_UN: a child inheriting this open file description retains
            # the lease after its parent dies, until the child actually exits.
            os.close(fd)

    def _generation(self, name):
        if not isinstance(name, str) or not _GEN.fullmatch(name):
            raise GbrainError('gbrain_cache_generation_invalid')
        path = _path(self.generations / name)
        if _read(path / 'generation.json') != {'kind': self.KIND, 'owner': self.owner['owner'], 'generation': name}:
            raise GbrainError('gbrain_cache_generation_invalid')
        return path

    def active(self):
        path = _path(self.root / 'active.json')
        if not path.exists():
            return None
        value = _read(path)
        if (not isinstance(value, dict) or set(value) != {'owner', 'generation', 'fingerprint'}
                or value['owner'] != self.owner['owner'] or not isinstance(value['fingerprint'], str)
                or not _HEX.fullmatch(value['fingerprint'])):
            raise GbrainError('gbrain_cache_record_invalid')
        generation = self._generation(value['generation'])
        ready = _read(generation / 'ready.json')
        manifest = generation / 'projection.json'
        if (not isinstance(ready, dict) or ready.get('fingerprint') != value['fingerprint']
                or ready.get('manifest_sha256') != _sha(_bytes(manifest, MANIFEST_BYTES))):
            raise GbrainError('gbrain_cache_record_invalid')
        return value

    def _retirement_record(self):
        retiring_path = self.root / 'retiring.json'
        retiring = _read(retiring_path) if retiring_path.exists() else {'owner': self.owner['owner'], 'generations': []}
        if (not isinstance(retiring, dict) or set(retiring) != {'owner', 'generations'}
                or retiring['owner'] != self.owner['owner'] or not isinstance(retiring['generations'], list)
                or len(retiring['generations']) > 256
                or any(not isinstance(n, str) or not _GEN.fullmatch(n) for n in retiring['generations'])
                or len(set(retiring['generations'])) != len(retiring['generations'])):
            raise GbrainError('gbrain_cache_record_invalid')
        return retiring

    def _remove(self, name):
        retiring_path = self.root / 'retiring.json'
        retiring = self._retirement_record()
        if not isinstance(name, str) or not _GEN.fullmatch(name):
            raise GbrainError('gbrain_cache_generation_invalid')
        path = _path(self.generations / name)
        if name in retiring['generations'] and not path.exists():
            retiring['generations'].remove(name)
            _write(retiring_path, retiring)
            return
        if name not in retiring['generations'] or (path / 'generation.json').exists():
            self._generation(name)
        if not path.is_dir():
            raise GbrainError('gbrain_cache_generation_invalid')
        if any(p.is_symlink() for p in path.rglob('*')):
            raise GbrainError('gbrain_cache_generation_unsafe')
        # Keep deletion authority outside the directory being removed. Recovery
        # can finish after a crash that already removed generation.json.
        if name not in retiring['generations']:
            retiring['generations'].append(name)
            _write(retiring_path, retiring)
        shutil.rmtree(path)
        _sync(self.generations)
        retiring['generations'].remove(name)
        _write(retiring_path, retiring)

    def _retire(self, active, *, preserve=None):
        if preserve is None or active is not None and active['generation'] != preserve:
            _path(self.root / 'active.json').unlink(missing_ok=True)
            _sync(self.root)
        removed = 0
        names = {p.name for p in self.generations.iterdir()} | set(self._retirement_record()['generations'])
        for name in sorted(names):
            if name != preserve:
                existed = (self.generations / name).exists()
                self._remove(name); removed += int(existed)
        return removed

    def withdraw(self, *, check=lambda: None):
        with self.lease(exclusive=True, check=check):
            removed = self._retire(None)
            return {'ok': True, 'state': 'withdrawn', 'removed_generations': removed}

    def _command(self, generation, args, *, fd, check, write=False):
        check()
        operation = generation / 'operation.json'
        prior = _read(operation) if operation.exists() else {'step': 0}
        step = prior['step'] + 1
        request = str(uuid.uuid5(uuid.UUID(self.owner['owner']), generation.name + ':' + str(step)))
        record = {'step': step, 'operation': args[0], 'request_id': request if write else None, 'state': 'intent'}
        _write(operation, record)
        env = {k: os.environ[k] for k in ('PATH', 'LANG', 'LC_ALL', 'TZ', 'HOME', 'TMPDIR') if k in os.environ}
        env.update(GBRAIN_HOME=str(generation / 'home'), GBRAIN_AUDIT_DIR=str(generation / 'audit'),
                   GBRAIN_SOURCE='default', GBRAIN_SWEEP='0', GBRAIN_SERVE_SYNC_IPC='0')
        argv = [str(self.root / 'runtime/gbrain'), *args] + (['--request-id', request] if write else [])
        if sum(len(a.encode()) + 1 for a in argv) > 128 * 1024:
            raise GbrainError('gbrain_cache_command_budget')
        process = subprocess.Popen(argv, cwd=generation, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True, pass_fds=(fd,))
        output = {process.stdout: bytearray(), process.stderr: bytearray()}
        streams, until = list(output), time.monotonic() + 60
        try:
            while streams:
                check()
                if time.monotonic() >= until:
                    raise GbrainError('gbrain_cache_command_deadline')
                ready, _, _ = select.select(streams, [], [], .05)
                for stream in ready:
                    data = os.read(stream.fileno(), 65536)
                    if not data:
                        streams.remove(stream)
                    else:
                        output[stream].extend(data)
                        if len(output[stream]) > 8 * 1024 * 1024:
                            raise GbrainError('gbrain_cache_command_budget')
            code = process.wait(timeout=2)
            if code != 0:
                raise GbrainError('gbrain_cache_command_failed')
            record.update(state='returned', exit_code=code)
            _write(operation, record)
            return bytes(output[process.stdout])
        finally:
            if process.poll() is None:
                if os.getpgid(process.pid) == process.pid:
                    os.killpg(process.pid, signal.SIGKILL)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    raise _UnreapedWorker('gbrain_cache_worker_not_reaped') from None
            process.stdout.close(); process.stderr.close()

    def _build(self, generation, projection, *, fd, check):
        validate_binary(self.root / 'runtime-receipt.json', self.root, check)
        def command(args, *, write=False, text=False):
            raw = self._command(generation, args, fd=fd, check=check, write=write)
            value = raw.decode() if text else _json(raw)
            if not text and isinstance(value, dict) and (value.get('ok') is False or value.get('error')):
                raise GbrainError('gbrain_cache_operation_failed')
            return value
        command(['init', '--pglite', '--no-embedding', '--db-only', '--path', str(generation / 'brain.pglite'),
                 '--non-interactive', '--search-mode', 'conservative', '--json'])
        pack = generation / 'home/.gbrain/schema-packs/alden-scoped-graph'
        _path(pack); pack.mkdir(parents=True, mode=0o700)
        (pack / 'pack.yaml').write_text(projection.schema_yaml())
        if 'valid manifest' not in command(['schema', 'validate', 'alden-scoped-graph'], text=True):
            raise GbrainError('gbrain_cache_schema_invalid')
        command(['schema', 'use', 'alden-scoped-graph'], text=True)
        schema = command(['schema', 'active', '--source', 'default', '--json'])
        if not isinstance(schema, dict) or schema.get('pack') != 'alden-scoped-graph' or schema.get('degraded'):
            raise GbrainError('gbrain_cache_schema_invalid')
        # Prove the initialized config still meets the reviewed keyless contract
        # before passing any original graph label to the runtime.
        validate_runtime(self.root / 'runtime-receipt.json', self.root, generation / 'home', check)
        pages = projection.pages()
        revisions = {}
        for offset in range(0, len(pages), 10):
            batch = pages[offset:offset+10]
            saved = command(['put-pages', '--pages', json.dumps(batch, ensure_ascii=False),
                             '--source-id', 'default', '--json'], write=True)
            if (not isinstance(saved, dict) or saved.get('state') != 'committed' or saved.get('terminal') is not True
                    or saved.get('source_id') != 'default' or not isinstance(saved.get('pages'), list)
                    or len(saved['pages']) != len(batch)
                    or not isinstance(saved.get('counts'), dict)
                    or any(type(value) is not int for value in saved['counts'].values())
                    or saved.get('counts') != {'total': len(batch), 'committed': len(batch), 'pending': 0, 'failed': 0}):
                raise GbrainError('gbrain_cache_batch_uncommitted')
            for index, (expected_page, page) in enumerate(zip(batch, saved['pages'])):
                if (not isinstance(page, dict) or type(page.get('index')) is not int or page['index'] != index
                        or page.get('slug') != expected_page['slug'] or page.get('state') != 'committed'
                        or page.get('embedding_state') != 'disabled' or not isinstance(page.get('revision'), str)
                        or not 1 <= len(page['revision']) <= 256 or any(ord(c) < 32 for c in page['revision'])):
                    raise GbrainError('gbrain_cache_batch_uncommitted')
                revisions[page['slug']] = page['revision']
        for link in projection.links():
            command(['link', link['from'], link['to'], '--link-type', link['link_type'],
                     '--link-source', link['link_source'], '--context', link['context'], '--json'], write=True)
        for page in pages:
            result = command(['get', page['slug'], '--source-id', 'default', '--json'])
            node = projection.by_slug[page['slug']]
            expected = {'alden_node_id': node['id'], 'alden_snapshot': projection.fingerprint,
                        'alden_source_target': node['source_target']}
            if 'source_version' in node:
                expected['alden_source_version'] = node['source_version']
            if (not isinstance(result, dict) or result.get('slug') != page['slug']
                    or result.get('title') != node['label']
                    or result.get('source_id') != 'default' or result.get('revision') != revisions[page['slug']]
                    or not isinstance(result.get('frontmatter'), dict)
                    or any(result['frontmatter'].get(k) != v for k, v in expected.items())):
                raise GbrainError('gbrain_cache_readback_invalid')
        with GbrainReadClient(self.root / 'runtime-receipt.json', owned_root=self.root,
                             home=generation / 'home', check=check, lease_fd=fd) as client:
            for focus in projection.nodes:
                rows = client.graph(projection.slugs[focus], depth=1)
                projection.bind(rows, focus=focus, current_packet=projection.snapshot,
                                projects=projection.projects, depth=1)
        return {'pages': len(pages), 'links': len(projection.links()), 'verified_queries': len(projection.nodes)}

    def _current_projection(self, current):
        try:
            packet = current()
        except ValueError as error:
            # The canonical API removes projects with no permitted targets.
            # A formerly bound scope disappearing is withdrawal, not an empty
            # successful import and not an arbitrary I/O exception.
            if str(error) == 'collection_project_scope_invalid':
                self._retire(None)
                raise GbrainError('gbrain_cache_scope_withdrawn') from None
            raise
        if isinstance(packet, dict) and packet.get('ok') is True and packet.get('state') == 'not_configured':
            self._retire(None)
            raise GbrainError('gbrain_cache_source_unavailable')
        return GbrainProjection(packet, self.owner['projects'])

    def refresh(self, *, current, check=lambda: None, rebuild=False):
        with self.lease(exclusive=True, check=check) as fd:
            projection = self._current_projection(current)
            manifest = projection.manifest()
            active = self.active()
            if active and active['fingerprint'] == projection.fingerprint and not rebuild:
                removed = self._retire(active, preserve=active['generation'])
                if self._current_projection(current).fingerprint != projection.fingerprint:
                    self._retire(None)
                    raise GbrainError('gbrain_projection_stale')
                return {'ok': True, 'state': 'unchanged', 'fingerprint': projection.fingerprint,
                        'removed_generations': removed}
            removed = self._retire(active)
            name = 'g' + uuid.uuid4().hex
            generation = self.generations / name
            generation.mkdir(mode=0o700)
            _write(generation / 'generation.json', {'kind': self.KIND, 'owner': self.owner['owner'], 'generation': name})
            try:
                acceptance = self._build(generation, projection, fd=fd, check=check)
                check()
                if self._current_projection(current).fingerprint != projection.fingerprint:
                    raise GbrainError('gbrain_projection_stale')
                _write(generation / 'projection.json', manifest)
                _write(generation / 'ready.json', {'fingerprint': projection.fingerprint,
                    'manifest_sha256': _sha((generation / 'projection.json').read_bytes()), **acceptance})
                check()
                _write(self.root / 'active.json', {'owner': self.owner['owner'],
                    'generation': name, 'fingerprint': projection.fingerprint})
                # Independent readback also covers uncertain pointer acceptance.
                if self.active()['generation'] != name:
                    raise GbrainError('gbrain_cache_publish_invalid')
                if self._current_projection(current).fingerprint != projection.fingerprint:
                    raise GbrainError('gbrain_projection_stale')
                return {'ok': True, 'state': 'published', 'fingerprint': projection.fingerprint,
                        'removed_generations': removed, **acceptance}
            except _UnreapedWorker:
                raise  # Child retains the lease. Recovery cannot remove its DB.
            except BaseException:
                self._retire(None)
                raise


class ManagedGbrainReader(GbrainGraphReader):
    def __init__(self, root, *, collection_root, projects):
        self.cache = ManagedGbrainCache(root, collection_root=collection_root, projects=projects)

    def query(self, *, projects, focus, current, check=lambda: None, depth=2, direction='both'):
        if _projects(projects) != self.cache.owner['projects']:
            raise GbrainError('gbrain_manifest_scope_mismatch')
        with self.cache.lease(exclusive=False, check=check) as fd:
            active = self.cache.active()
            if active is None:
                raise GbrainError('gbrain_cache_unavailable')
            generation = self.cache._generation(active['generation'])
            reader = GbrainGraphReader(owned_root=self.cache.root,
                receipt=self.cache.root / 'runtime-receipt.json', home=generation / 'home',
                manifest=generation / 'projection.json', lease_fd=fd)
            return reader.query(projects=projects, focus=focus, current=current,
                                check=check, depth=depth, direction=direction)


def main():
    import argparse
    from alden_abort import ABORT_STATE_NAME, AbortToken, AldenCancelled
    from alden_collection import read_action
    from alden_knowledge_mcp import KnowledgeServer
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('create', 'refresh', 'withdraw'))
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--state-root', type=Path, required=True)
    parser.add_argument('--project', action='append', required=True)
    parser.add_argument('--runtime-root', type=Path)
    parser.add_argument('--runtime-receipt', type=Path)
    parser.add_argument('--runtime-home', type=Path)
    parser.add_argument('--messenger-source', choices=('kakaotalk',))
    parser.add_argument('--rebuild', action='store_true')
    args = parser.parse_args()
    runtime = (args.runtime_root, args.runtime_receipt, args.runtime_home)
    if args.action == 'create' and not all(runtime) or args.action != 'create' and any(runtime):
        parser.error('runtime paths are required together, only for create')
    if args.rebuild and args.action != 'refresh':
        parser.error('--rebuild requires refresh')
    token = AbortToken(args.state_root / ABORT_STATE_NAME)
    until = time.monotonic() + 300
    def check():
        token.raise_if_cancelled()
        if time.monotonic() >= until:
            raise GbrainError('gbrain_cache_deadline')
    try:
        check()
        if args.action == 'withdraw':
            # Withdrawal is confined to this bound owned cache. Lost access to
            # the original provider must not require re-enrollment to erase it.
            cache = ManagedGbrainCache(args.root, collection_root=args.state_root, projects=args.project)
            print(json.dumps(cache.withdraw(check=check)))
            return
        factory = None
        if args.messenger_source:
            from alden_messenger_factory import open_existing_kakao
            factory = open_existing_kakao
        authority = KnowledgeServer(args.state_root, args.project, source_factory=factory)
        def current():
            check()
            guards = authority._source_guards(token.is_cancelled)
            return read_action(args.state_root, 'collection-graph',
                json.dumps({'projects': args.project, 'limit': 50}), cancelled=token.is_cancelled,
                source_access=guards['source_access'], source_revision=guards['source_revision'],
                source_relation_access=guards['source_relation_access'])
        if args.action == 'create':
            GbrainProjection(current(), args.project).manifest()
            ManagedGbrainCache.create(args.root, collection_root=args.state_root, projects=args.project,
                runtime_root=args.runtime_root, runtime_receipt=args.runtime_receipt,
                runtime_home=args.runtime_home, check=check)
            result = {'ok': True, 'state': 'created'}
        else:
            cache = ManagedGbrainCache(args.root, collection_root=args.state_root, projects=args.project)
            result = cache.refresh(current=current, check=check, rebuild=args.rebuild) if args.action == 'refresh' else cache.withdraw(check=check)
        print(json.dumps(result, ensure_ascii=False))
    except Exception as error:
        if isinstance(error, AldenCancelled):
            code = 'alden_global_abort'
        elif isinstance(error, PermissionError):
            code = 'gbrain_cache_permission_denied'
        elif isinstance(error, GbrainError) and re.fullmatch(r'gbrain_[a-z_]+', str(error)):
            code = str(error)
        else:
            code = 'gbrain_cache_unavailable'
        print(json.dumps({'ok': False, 'error': code}))
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
