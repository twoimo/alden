"""Real collection/MCP boundaries with controlled SDKs; never native source SQL."""
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import hashlib
import importlib
import json
import os
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
    def healthy():
        return {'status': 'installation_consistent', 'skill_integrity': {'status': 'verified'},
            **{k: 0 for k in ('chat_messages_read', 'identity_cache_reads', 'credential_reads', 'native_actions', 'send_calls', 'writes')}}

    def test_unverified_health_never_imports_a_reader(self):
        for report in [{}, {'status': 'installation_consistent', 'skill_integrity': 'verified'},
                       {**self.healthy(), 'writes': 1}, {**self.healthy(), 'skill_integrity': {'status': 'manifest_invalid'}}]:
            with patch.object(factory.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=json.dumps(report))), \
                 patch.object(factory, '_load_verified_sdk') as loader:
                with self.assertRaisesRegex(runtime.SourceBlocked, 'integrity_unverified'): factory.load_kakao_sdk()
                loader.assert_not_called()

    def test_healthy_fixture_module_origins_are_verified_and_a_later_file_change_is_not_reloaded(self):
        with TemporaryDirectory(dir='/private/tmp') as temporary, ExitStack() as stack:
            folder = Path(temporary)
            for name in factory.SDK_MODULES:
                (folder / (name + '.py')).write_text('')
            (folder / 'full_chat_reader.py').write_text('import sqlcipher_adapter,media_context,room_participants,message_visibility\n'
                'def resolve_identity(device): return ("owner", "fixture")\n'
                'def snapshot(**options): return {}\nsnapshot.supports_exact_confirmation=True\n')
            stack.enter_context(patch.dict(sys.modules))
            for name in factory.SDK_MODULES: sys.modules.pop(name, None)
            stack.enter_context(patch.dict(factory._loaded, {}, clear=True))
            stack.enter_context(patch.object(factory, 'SDK', folder))
            health = stack.enter_context(patch.object(factory.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=json.dumps(self.healthy()))))
            first = factory.load_kakao_sdk(); self.assertIs(factory.load_kakao_sdk(), first)
            self.assertEqual(health.call_count, 2)
            (folder / 'message_visibility.py').write_text('changed = True\n')
            with self.assertRaisesRegex(runtime.SourceBlocked, 'loaded_revision_unverified'): factory.load_kakao_sdk()

    def test_existing_unattested_module_cannot_supply_the_sdk(self):
        with TemporaryDirectory(dir='/private/tmp') as temporary:
            folder = Path(temporary)
            for name in factory.SDK_MODULES: (folder / (name + '.py')).write_text('')
            with patch.object(factory, 'SDK', folder), patch.dict(factory._loaded, {}, clear=True), \
                 patch.dict(sys.modules, {'sqlcipher_adapter': SimpleNamespace()}), \
                 patch.object(factory.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=json.dumps(self.healthy()))):
                with self.assertRaisesRegex(runtime.SourceBlocked, 'loaded_revision_unverified'): factory.load_kakao_sdk()
