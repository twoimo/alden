"""Real collection/MCP boundaries with controlled SDKs; never native source SQL."""
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import hashlib
import importlib
import json
import os
import py_compile
import select
import shutil
import sqlite3
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import alden_messenger_runtime as runtime
import alden_messenger_factory as factory
from alden_knowledge_mcp import KnowledgeServer
from alden_status_mcp import CallControl, CallStopped


class SDK:
    owner = 'owner'
    body = '회의 자료 검토 일정은 화요일 오후 세 시로 결정되었습니다.'
    failure = None
    receipt = {}

    def resolve_identity(self, device):
        return self.owner, 'controlled_fixture'

    def snapshot(self, **options):
        return {'status': 'unavailable' if self.failure else 'ok', 'reason': self.failure,
            'db_readonly_verified': True, 'schema_decryption_verified': True,
            'server_calls': 0, 'send_calls': 0, 'database_row_writes': 0, 'app_settings_writes': 0,
            'chat': {'chatId': 'a'}, 'messages_truncated': False,
            'messages': [{'chatId': 'a', 'logId': '1', 'message': self.body, 'sentAt': 1001}], **self.receipt}


class ExistingFactoryTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(dir='/private/tmp'); self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name); self.root = runtime.create_root(self.folder / 'state')
        self.sdk = SDK()
        self.enterContext(patch.object(runtime, 'platform_uuid', return_value='controlled-device'))
        reader = runtime.KakaoReader(self.sdk)
        self.engine = runtime.Frontier(self.root, 'kakaotalk', 'owner', reader)
        self.engine.seed({'account_id': 'owner', 'complete': True, 'rooms': [{'id': 'a', 'title': 'fixture'}]})
        self.engine.step()
        self.load = self.enterContext(patch.object(factory, 'load_kakao_sdk', return_value=self.sdk))

    def server(self, **options):
        return KnowledgeServer(self.root, [runtime.PROJECT], source_factory=factory.open_existing_kakao,
            search_strategy='indexed-lexical', **options)

    def query(self, server, control=None):
        return server.call_tool({'name': 'alden_knowledge_search',
            'arguments': {'projects': [runtime.PROJECT], 'query': '회의', 'limit': 1}}, control or CallControl())

    def hashes(self):
        return {str(p.relative_to(self.root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.rglob('*.sqlite3')}

    def test_standard_server_reopens_readonly_and_returns_current_fact_context_without_writes(self):
        before = self.hashes(); authority = factory.open_existing_kakao(self.root)
        source = next(iter(authority.registry.sources.values()))[0]
        self.assertTrue(source.read_only and source.store.read_only)
        with source.database() as db:
            with self.assertRaisesRegex(sqlite3.OperationalError, 'readonly'): db.execute('DELETE FROM message_heads')
        result = self.query(self.server())
        self.assertEqual(len(result['facts']), 1)
        self.assertEqual(len(result['conversation_context']['items']), 1)
        self.assertEqual(self.hashes(), before)

    def test_each_request_rechecks_account_without_adopting_a_new_binding(self):
        server = self.server(); before = self.hashes(); self.sdk.owner = 'different'
        with self.assertRaisesRegex(ValueError, 'account_binding_invalid'):
            self.query(server)
        self.assertEqual(self.hashes(), before)

    def test_cancelled_request_stops_before_sdk_load_and_existing_authority_observes_late_cancel(self):
        server = self.server(); self.load.reset_mock()
        control = CallControl(); control.stopped.set()
        with self.assertRaises(CallStopped): self.query(server, control)
        self.load.assert_not_called()
        with self.assertRaisesRegex(runtime.SourceBlocked, 'request_cancelled'):
            factory.open_existing_kakao(self.root, lambda: True)
        self.load.assert_not_called()
        cancelled = [False]; authority = factory.open_existing_kakao(self.root, lambda: cancelled[0])
        cancelled[0] = True
        with self.assertRaisesRegex(runtime.SourceBlocked, 'request_cancelled'):
            authority.revision(self.engine.target)

    def test_receipt_failure_is_withheld_and_canonical_unavailability_keeps_its_reason(self):
        authority = factory.open_existing_kakao(self.root)
        source = next(iter(authority.registry.sources.values()))[0]
        for receipt in [{'send_calls': 1}, {'database_row_writes': False}, {'db_readonly_verified': False}]:
            self.sdk.receipt = receipt
            with self.assertRaisesRegex(runtime.SourceBlocked, 'readonly_receipt_invalid'):
                source.reader.page('a', None)
        self.sdk.receipt = {}; self.sdk.failure = 'current_database_path_unavailable'
        with self.assertRaisesRegex(runtime.SourceBlocked, 'kakao_reader_current_database_path_unavailable'):
            source.reader.page('a', None)

    def test_factory_root_is_pinned_and_cannot_mix_legacy_callbacks(self):
        server = self.server(); server.root = self.folder / 'other'; shutil.copytree(self.root, server.root)
        with self.assertRaisesRegex(ValueError, 'factory_root_changed'): self.query(server)
        with self.assertRaisesRegex(ValueError, 'factory_invalid'):
            self.server(source_access=lambda *args: True)
        with self.assertRaisesRegex(ValueError, 'factory_authority_invalid'):
            KnowledgeServer(self.root, [runtime.PROJECT], source_factory=lambda *args: SimpleNamespace())

    def test_sdk_qualification_is_rechecked_on_each_request(self):
        server = self.server(); before = self.hashes()
        self.load.side_effect = runtime.SourceBlocked('kakao_sdk_integrity_unverified')
        with self.assertRaisesRegex(runtime.SourceBlocked, 'integrity_unverified'): self.query(server)
        self.assertEqual(self.hashes(), before)

    def test_invalid_marker_is_rejected_before_sdk_and_fifo_does_not_block(self):
        marker = self.root / '.messenger-evaluation'; marker.unlink(); os.mkfifo(marker)
        self.load.reset_mock()
        with self.assertRaisesRegex(ValueError, 'evaluation_root_required'):
            factory.open_existing_kakao(self.root)
        self.load.assert_not_called()

    def test_legacy_imports_share_the_maintained_runtime_and_policy(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'docs/evaluation/messenger-stream-20261021'))
        self.addCleanup(sys.path.pop, 0)
        self.assertIs(importlib.import_module('messenger_candidate'), runtime)
        self.assertIs(importlib.import_module('messenger_quality'), importlib.import_module('alden_messenger_quality'))

    def test_real_cli_stdio_uses_factory_and_rechecks_account_with_a_controlled_sdk(self):
        repo = Path(__file__).resolve().parents[1]; owner = self.folder / 'owner.txt'; owner.write_text('owner')
        script = '''import sys,runpy,socket
from pathlib import Path
from unittest.mock import patch
repo=Path(sys.argv[1]);sys.path[:0]=[str(repo/'scripts'),str(repo)]
import alden_messenger_factory as factory,alden_messenger_runtime as runtime
from tests.test_alden_messenger_factory import SDK
sdk=SDK();owner=Path(sys.argv[3]);sdk.resolve_identity=lambda device:(owner.read_text(),'fixture')
runtime.platform_uuid=lambda:'controlled-device'
factory.load_kakao_sdk=lambda:sdk
sys.argv=[str(repo/'scripts/alden_knowledge_mcp.py'),'--state-root',sys.argv[2],
 '--allow-project',runtime.PROJECT,'--messenger-source','kakaotalk','--search-strategy','indexed-lexical']
with patch.object(socket.socket,'connect',side_effect=AssertionError('unexpected network')):
 runpy.run_path(sys.argv[0],run_name='__main__')
'''
        before = self.hashes(); buffer = bytearray()
        child = subprocess.Popen([sys.executable, '-B', '-c', script, str(repo), str(self.root), str(owner)],
            cwd=repo, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        def rpc(value):
            child.stdin.write((json.dumps(value) + '\n').encode()); child.stdin.flush()
            if 'id' not in value: return
            deadline = time.monotonic() + 5
            while b'\n' not in buffer:
                if time.monotonic() >= deadline: self.fail('controlled stdio response deadline')
                if select.select([child.stdout], [], [], .1)[0]:
                    data = os.read(child.stdout.fileno(), 65536)
                    if not data: self.fail('unexpected controlled child EOF')
                    buffer.extend(data)
            line, _, remaining = buffer.partition(b'\n'); buffer[:] = remaining
            value_out = json.loads(line); self.assertEqual(value_out['id'], value['id']); return value_out
        def lookup(identity):
            return rpc({'jsonrpc': '2.0', 'id': identity, 'method': 'tools/call', 'params': {
                'name': 'alden_knowledge_search', 'arguments': {'projects': [runtime.PROJECT], 'query': '회의', 'limit': 1}}})
        try:
            self.assertIn('result', rpc({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
                'protocolVersion': '2025-11-25', 'capabilities': {}, 'clientInfo': {'name': 'fixture', 'version': '1'}}}))
            rpc({'jsonrpc': '2.0', 'method': 'notifications/initialized'})
            result = lookup(2)['result']; self.assertFalse(result['isError'])
            contents = json.loads(result['content'][0]['text']); self.assertEqual(len(contents['facts']), 1)
            self.assertEqual(len(contents['conversation_context']['items']), 1)
            owner.write_text('different'); self.assertTrue(lookup(3)['result']['isError'])
            owner.write_text('owner'); self.assertFalse(lookup(4)['result']['isError'])
            self.assertIn('result', rpc({'jsonrpc': '2.0', 'id': 5, 'method': 'ping'}))
        finally:
            child.stdin.close()
            try: child.wait(timeout=5)
            except subprocess.TimeoutExpired: child.terminate(); child.wait(timeout=5)
            errors = child.stderr.read(); child.stdout.close(); child.stderr.close()
        self.assertEqual(child.returncode, 0, errors.decode())
        self.assertEqual(self.hashes(), before)


class CanonicalLoaderTests(unittest.TestCase):
    @staticmethod
    def source_fixture(folder):
        for name in factory.SDK_MODULES: (folder / (name + '.py')).write_text('')
        source = folder / 'full_chat_reader.py'
        source.write_text('import sqlcipher_adapter,media_context,room_participants,message_visibility\n'
            'def resolve_identity(device): return ("verified-source", "fixture")\n'
            'def snapshot(**options):\n import message_confirmation,emoticon_confirmation,room_catalog\n return {}\n'
            'snapshot.supports_exact_confirmation=True\n')
        return source

    def isolate_modules(self, stack, folder):
        stack.enter_context(patch.dict(sys.modules))
        for name in factory.SDK_MODULES: sys.modules.pop(name,None)
        stack.enter_context(patch.dict(factory._loaded,{},clear=True))
        stack.enter_context(patch.object(factory,'SDK',folder))
        return stack.enter_context(patch.object(factory.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(self.healthy()))))

    @staticmethod
    def healthy():
        return {'status': 'installation_consistent', 'skill_integrity': {'status': 'verified'},
            **{k: 0 for k in ('chat_messages_read', 'identity_cache_reads', 'credential_reads', 'native_actions', 'send_calls', 'writes')}}

    def test_unchecked_bytecode_cache_cannot_replace_verified_reader_source(self):
        with TemporaryDirectory(dir='/private/tmp') as temporary, ExitStack() as stack:
            folder = Path(temporary)
            for name in factory.SDK_MODULES: (folder / (name + '.py')).write_text('')
            source = folder / 'full_chat_reader.py'
            source.write_text('import sqlcipher_adapter,media_context,room_participants,message_visibility\n'
                'def resolve_identity(device): return ("verified-source", "fixture")\n'
                'def snapshot(**options):\n import message_confirmation,emoticon_confirmation,room_catalog\n return {}\n'
                'snapshot.supports_exact_confirmation=True\n')
            unrelated = folder / 'cache_input.py'
            unrelated.write_text('def resolve_identity(device): return ("unverified-cache", "fixture")\n'
                'def snapshot(**options): return {}\nsnapshot.supports_exact_confirmation=True\n')
            cache = Path(importlib.util.cache_from_source(str(source)))
            cache.parent.mkdir()
            py_compile.compile(str(unrelated),cfile=str(cache),doraise=True,
                invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)
            before = {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.glob('*.py')}
            stack.enter_context(patch.dict(sys.modules))
            for name in factory.SDK_MODULES: sys.modules.pop(name,None)
            stack.enter_context(patch.dict(factory._loaded,{},clear=True))
            stack.enter_context(patch.object(factory,'SDK',folder))
            stack.enter_context(patch.object(factory.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(self.healthy()))))
            module = factory.load_kakao_sdk()
            self.assertEqual(module.resolve_identity(None)[0],'verified-source')
            self.assertEqual(before,{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.glob('*.py')})

    def test_dependency_cache_is_ignored_and_import_path_stays_unchanged(self):
        with TemporaryDirectory(dir='/private/tmp') as temporary, ExitStack() as stack:
            folder=Path(temporary);source=self.source_fixture(folder)
            (folder/'message_visibility.py').write_text('ORIGIN="source"\n')
            source.write_text(source.read_text().replace('"verified-source"','message_visibility.ORIGIN'))
            unrelated=folder/'cache_input.py';unrelated.write_text('ORIGIN="cache"\n')
            cache=Path(importlib.util.cache_from_source(str(folder/'message_visibility.py')));cache.parent.mkdir()
            py_compile.compile(str(unrelated),cfile=str(cache),doraise=True,invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)
            self.isolate_modules(stack,folder);before=list(sys.path)
            module=factory.load_kakao_sdk()
            self.assertEqual(module.resolve_identity(None)[0],'source')
            self.assertEqual(sys.path,before)
            self.assertTrue(all(sys.modules[name].__cached__ is None for name in factory.SDK_MODULES))

    def test_source_changed_during_qualification_is_never_executed(self):
        with TemporaryDirectory(dir='/private/tmp') as temporary, ExitStack() as stack:
            folder=Path(temporary);source=self.source_fixture(folder);health=self.isolate_modules(stack,folder)
            def changed(*args,**kwargs):
                source.write_text(source.read_text().replace('verified-source','changed-after-capture'))
                return SimpleNamespace(returncode=0,stdout=json.dumps(self.healthy()))
            health.side_effect=changed
            with self.assertRaisesRegex(runtime.SourceBlocked,'loaded_revision_unverified'):factory.load_kakao_sdk()
            self.assertEqual(factory._loaded,{})
            self.assertTrue(all(name not in sys.modules for name in factory.SDK_MODULES))

    def test_failed_initialization_rolls_back_only_owned_modules_and_can_retry(self):
        with TemporaryDirectory(dir='/private/tmp') as temporary, ExitStack() as stack:
            folder=Path(temporary);self.source_fixture(folder);self.isolate_modules(stack,folder)
            (folder/'media_context.py').write_text('raise RuntimeError("controlled-initialization-failure")\n')
            sentinel=SimpleNamespace();sys.modules['unrelated_owner_fixture']=sentinel
            before=list(sys.path)
            with self.assertRaisesRegex(RuntimeError,'controlled-initialization-failure'):factory.load_kakao_sdk()
            self.assertTrue(all(name not in sys.modules for name in factory.SDK_MODULES))
            self.assertEqual(factory._loaded,{})
            self.assertIs(sys.modules['unrelated_owner_fixture'],sentinel)
            self.assertEqual(sys.path,before)
            (folder/'media_context.py').write_text('')
            self.assertEqual(factory.load_kakao_sdk().resolve_identity(None)[0],'verified-source')

    def test_failed_contract_never_becomes_a_loaded_attestation(self):
        with TemporaryDirectory(dir='/private/tmp') as temporary, ExitStack() as stack:
            folder=Path(temporary);source=self.source_fixture(folder);self.isolate_modules(stack,folder)
            source.write_text(source.read_text().replace('supports_exact_confirmation=True','supports_exact_confirmation=False'))
            with self.assertRaisesRegex(runtime.SourceBlocked,'contract_unavailable'):factory.load_kakao_sdk()
            self.assertEqual(factory._loaded,{})
            self.assertTrue(all(name not in sys.modules for name in factory.SDK_MODULES))

    def test_source_replaced_by_its_initialization_is_rejected_without_attestation(self):
        with TemporaryDirectory(dir='/private/tmp') as temporary, ExitStack() as stack:
            folder=Path(temporary);source=self.source_fixture(folder);self.isolate_modules(stack,folder)
            source.write_text(source.read_text()+'\nfrom pathlib import Path\nPath(__file__).write_text("changed=1\\n")\n')
            with self.assertRaisesRegex(runtime.SourceBlocked,'loaded_revision_unverified'):factory.load_kakao_sdk()
            self.assertEqual(factory._loaded,{})
            self.assertTrue(all(name not in sys.modules for name in factory.SDK_MODULES))

    def test_dependency_cycle_is_an_explicit_unsupported_contract(self):
        with TemporaryDirectory(dir='/private/tmp') as temporary, ExitStack() as stack:
            folder=Path(temporary);self.source_fixture(folder);self.isolate_modules(stack,folder)
            (folder/'media_context.py').write_text('import full_chat_reader\n')
            with self.assertRaisesRegex(runtime.SourceBlocked,'dependency_cycle'):factory.load_kakao_sdk()
            self.assertEqual(factory._loaded,{})
            self.assertTrue(all(name not in sys.modules for name in factory.SDK_MODULES))

    def test_nonregular_or_oversized_sources_stop_before_qualification(self):
        for kind in ('fifo','symlink','oversized'):
            with self.subTest(kind=kind),TemporaryDirectory(dir='/private/tmp') as temporary,ExitStack() as stack:
                folder=Path(temporary);self.source_fixture(folder);health=self.isolate_modules(stack,folder)
                source=folder/'message_visibility.py';source.unlink()
                if kind=='fifo':os.mkfifo(source)
                elif kind=='symlink':source.symlink_to(folder/'media_context.py')
                else:source.write_bytes(b'#'*(1024*1024+1))
                with self.assertRaisesRegex(runtime.SourceBlocked,'module_unavailable'):factory.load_kakao_sdk()
                health.assert_not_called()

    def test_deleted_owned_module_is_not_silently_reloaded(self):
        with TemporaryDirectory(dir='/private/tmp') as temporary, ExitStack() as stack:
            folder=Path(temporary);self.source_fixture(folder);self.isolate_modules(stack,folder)
            module=factory.load_kakao_sdk();original=factory._loaded.copy()
            sys.modules.pop('message_visibility')
            with self.assertRaisesRegex(runtime.SourceBlocked,'loaded_revision_unverified'):factory.load_kakao_sdk()
            self.assertEqual(factory._loaded,original)
            self.assertIs(sys.modules['full_chat_reader'],module)

    def test_unverified_health_never_imports_a_reader(self):
        for report in [{}, {'status': 'installation_consistent', 'skill_integrity': 'verified'},
                       {**self.healthy(), 'writes': 1}, {**self.healthy(), 'skill_integrity': {'status': 'manifest_invalid'}}]:
            # Reach the health-report boundary using owned source fixtures.
            # A missing developer SDK correctly fails at the earlier file gate.
            with TemporaryDirectory(dir='/private/tmp') as temporary, ExitStack() as stack:
                folder = Path(temporary)
                self.source_fixture(folder)
                health = self.isolate_modules(stack, folder)
                health.return_value = SimpleNamespace(returncode=0, stdout=json.dumps(report))
                loader = stack.enter_context(patch.object(factory, '_load_verified_sdk'))
                with self.assertRaisesRegex(runtime.SourceBlocked, 'integrity_unverified'): factory.load_kakao_sdk()
                health.assert_called_once()
                loader.assert_not_called()

    def test_healthy_fixture_module_origins_are_verified_and_a_later_file_change_is_not_reloaded(self):
        with TemporaryDirectory(dir='/private/tmp') as temporary, ExitStack() as stack:
            folder = Path(temporary)
            for name in factory.SDK_MODULES:
                (folder / (name + '.py')).write_text('')
            (folder / 'full_chat_reader.py').write_text('import sqlcipher_adapter,media_context,room_participants,message_visibility\n'
                'def resolve_identity(device): return ("owner", "fixture")\n'
                'def snapshot(**options):\n import message_confirmation,emoticon_confirmation,room_catalog\n return {}\n'
                'snapshot.supports_exact_confirmation=True\n')
            stack.enter_context(patch.dict(sys.modules))
            for name in factory.SDK_MODULES: sys.modules.pop(name, None)
            stack.enter_context(patch.dict(factory._loaded, {}, clear=True))
            stack.enter_context(patch.object(factory, 'SDK', folder))
            health = stack.enter_context(patch.object(factory.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=json.dumps(self.healthy()))))
            first = factory.load_kakao_sdk(); self.assertIs(factory.load_kakao_sdk(), first)
            self.assertEqual(health.call_count, 2)
            self.assertIn('message_confirmation', factory._loaded)
            (folder / 'message_confirmation.py').write_text('changed = True\n')
            with self.assertRaisesRegex(runtime.SourceBlocked, 'loaded_revision_unverified'): factory.load_kakao_sdk()

    def test_existing_unattested_module_cannot_supply_the_sdk(self):
        with TemporaryDirectory(dir='/private/tmp') as temporary:
            folder = Path(temporary)
            for name in factory.SDK_MODULES: (folder / (name + '.py')).write_text('')
            with patch.object(factory, 'SDK', folder), patch.dict(factory._loaded, {}, clear=True), \
                 patch.dict(sys.modules, {'sqlcipher_adapter': SimpleNamespace()}), \
                 patch.object(factory.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=json.dumps(self.healthy()))):
                with self.assertRaisesRegex(runtime.SourceBlocked, 'loaded_revision_unverified'): factory.load_kakao_sdk()

    def test_new_conditional_dependency_is_rejected_before_import(self):
        with TemporaryDirectory(dir='/private/tmp') as temporary:
            folder = Path(temporary)
            for name in factory.SDK_MODULES: (folder / (name + '.py')).write_text('')
            (folder / 'full_chat_reader.py').write_text('def snapshot():\n import unexpected_fixture_dependency\n')
            (folder / 'unexpected_fixture_dependency.py').write_text("raise AssertionError('must not execute')\n")
            with patch.object(factory, 'SDK', folder), patch.dict(factory._loaded, {}, clear=True), \
                 patch.dict(sys.modules), patch.object(factory.importlib, 'import_module') as loader, \
                 patch.object(factory.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=json.dumps(self.healthy()))):
                for name in factory.SDK_MODULES: sys.modules.pop(name, None)
                with self.assertRaisesRegex(runtime.SourceBlocked, 'dependency_contract_changed'): factory.load_kakao_sdk()
                loader.assert_not_called()
