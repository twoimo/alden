"""Explicit read-only opening of existing enrollment; never discover or register."""
import ast
import hashlib
import importlib
import json
from pathlib import Path
import subprocess
import sys
import threading

from alden_abort import ABORT_STATE_NAME, AbortToken
from alden_messenger_runtime import Frontier, KakaoReader, MessengerAuthority, SourceBlocked, existing_messenger_root

SDK = Path.home() / '.agents/skills/aside-kakao-local-db/scripts'
SDK_MODULES = ('sqlcipher_adapter', 'media_context', 'room_participants', 'message_visibility',
               'emoticon_confirmation', 'message_confirmation', 'room_catalog', 'full_chat_reader')
_loaded = {}
_import_lock = threading.Lock()


def load_kakao_sdk():
    if not sys.dont_write_bytecode:
        raise SourceBlocked('kakao_sdk_requires_no_bytecode')
    try:
        result = subprocess.run([sys.executable, '-B', str(SDK / 'skill_health.py')],
            capture_output=True, text=True, timeout=10, check=False)
        report = json.loads(result.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError) as error:
        raise SourceBlocked('kakao_sdk_integrity_unverified') from error
    if (result.returncode != 0 or not isinstance(report, dict)
        or report.get('status') != 'installation_consistent'
        or not isinstance(report.get('skill_integrity'), dict)
        or report['skill_integrity'].get('status') != 'verified'
        or any(type(report.get(key)) is not int or report[key] != 0 for key in
               ('chat_messages_read', 'identity_cache_reads', 'credential_reads', 'native_actions', 'send_calls', 'writes'))):
        raise SourceBlocked('kakao_sdk_integrity_unverified')
    with _import_lock:
        return _load_verified_sdk()


def _load_verified_sdk():
    folder = SDK.resolve(strict=True)
    fingerprints, sources = {}, {}
    for name in SDK_MODULES:
        path = folder / (name + '.py')
        if path.is_symlink() or not path.is_file():
            raise SourceBlocked('kakao_sdk_module_unavailable')
        sources[name] = path.read_bytes()
        fingerprint = hashlib.sha256(sources[name]).hexdigest()
        fingerprints[name] = (path, fingerprint)
        present = sys.modules.get(name)
        if present is not None and _loaded.get(name) != (present, fingerprint):
            raise SourceBlocked('kakao_sdk_loaded_revision_unverified')
    # Function-local imports participate too. A changed dependency contract
    # requires review rather than silently adding an untracked lazy module.
    pending, closure = ['full_chat_reader'], set()
    while pending:
        name = pending.pop()
        if name in closure:
            continue
        if name not in sources:
            raise SourceBlocked('kakao_sdk_dependency_contract_changed')
        closure.add(name)
        try:
            tree = ast.parse(sources[name])
        except (SyntaxError, ValueError) as error:
            raise SourceBlocked('kakao_sdk_dependency_contract_changed') from error
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level:
                raise SourceBlocked('kakao_sdk_dependency_contract_changed')
            imports = [alias.name.split('.')[0] for alias in node.names] if isinstance(node, ast.Import) else (
                [node.module.split('.')[0]] if isinstance(node, ast.ImportFrom) and node.module else [])
            pending.extend(value for value in imports if (folder / (value + '.py')).is_file() or (folder / value).is_dir())
    if closure != set(SDK_MODULES):
        raise SourceBlocked('kakao_sdk_dependency_contract_changed')
    sys.path.insert(0, str(folder))
    try:
        module = importlib.import_module('full_chat_reader')
        for name in SDK_MODULES:
            importlib.import_module(name)
    finally:
        sys.path.remove(str(folder))
    for name, (path, fingerprint) in fingerprints.items():
        present = sys.modules.get(name)
        if present is None or Path(present.__file__).resolve() != path:
            raise SourceBlocked('kakao_sdk_module_origin_invalid')
        if hashlib.sha256(path.read_bytes()).hexdigest() != fingerprint:
            raise SourceBlocked('kakao_sdk_loaded_revision_unverified')
        _loaded[name] = (present, fingerprint)
    if (not callable(getattr(module, 'snapshot', None)) or not callable(getattr(module, 'resolve_identity', None))
        or getattr(module.snapshot, 'supports_exact_confirmation', False) is not True):
        raise SourceBlocked('kakao_sdk_contract_unavailable')
    return module


class _CanonicalRead:
    def __init__(self, module, check):
        self.module, self.check = module, check

    def resolve_identity(self, device):
        self.check(); value = self.module.resolve_identity(device); self.check()
        return value

    def snapshot(self, **options):
        self.check(); result = self.module.snapshot(**options); self.check()
        if (not isinstance(result, dict) or any(type(result.get(k)) is not int or result[k] != 0 for k in
                   ('server_calls', 'send_calls', 'database_row_writes', 'app_settings_writes'))):
            raise SourceBlocked('kakao_readonly_receipt_invalid')
        if result.get('status') == 'ok' and (result.get('db_readonly_verified') is not True or result.get('schema_decryption_verified') is not True):
            raise SourceBlocked('kakao_readonly_receipt_invalid')
        return result


class ExistingAuthority:
    def __init__(self, root, registry, cancelled):
        self.root, self.registry, self.cancelled = root, registry, cancelled
        self.abort = AbortToken(root / ABORT_STATE_NAME)

    def check(self):
        if self.cancelled() or self.abort.is_cancelled():
            raise SourceBlocked('messenger_request_cancelled')

    def collection_source_root(self):
        if self.registry.collection_source_root() != self.root:
            raise ValueError('messenger_authority_root_changed')
        return self.root

    def access(self, *args):
        self.check(); value = self.registry.access(*args); self.check()
        return value

    def revision(self, target):
        self.check(); value = self.registry.revision(target); self.check()
        return value

    def retrieval_context(self, proof, *, cancelled=lambda: False, **options):
        self.check()
        value = self.registry.retrieval_context(proof,
            cancelled=lambda: self.cancelled() or self.abort.is_cancelled() or cancelled(), **options)
        self.check()
        return value


def open_existing_kakao(root, cancelled=lambda: False):
    root = Path(root)
    if not root.is_absolute() or '..' in root.parts:
        raise ValueError('messenger_existing_absolute_root_required')
    existing_messenger_root(root)
    abort = AbortToken(root / ABORT_STATE_NAME)
    def check():
        if cancelled() or abort.is_cancelled():
            raise SourceBlocked('messenger_request_cancelled')
    check()
    canonical = _CanonicalRead(load_kakao_sdk(), check)
    check()
    reader = KakaoReader(canonical)
    source = Frontier.open_existing(root, 'kakaotalk', reader.identity(), reader)
    check()
    return ExistingAuthority(root, MessengerAuthority([source]), cancelled)
