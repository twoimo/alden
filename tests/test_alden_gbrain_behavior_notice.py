"""Passive disclosure and authority-request boundaries; synthetic RPCs only.

Run against a pinned current parent source/fixture snapshot, not this worktree's
old product source. Observed WHY is permitted notice-only protocol metadata;
all pages, receipts, sources and engine writes below are synthetic.
"""
import copy
import json
import unittest
from unittest.mock import patch

import alden_gbrain_client as client_module
import alden_gbrain_import as full
import alden_gbrain_delta as delta
from alden_collection_export import ExportError
from tests import test_alden_gbrain_import as fixtures


NATIVE_IMPORT_CLIENT = full.GbrainImportClient
DISCLOSURE = 'All stay on; this is a one-time disclosure, not a request for consent.'
COMMAND = 'gbrain doctor --only behavior_changes --json --brain host'
DOCS = 'https://github.com/garrytan/gbrain/blob/v0.60.451.0/docs/guides/chat-fallback.md'
SHORT_WHY = 'gbrain v0.60.448.0 changed 1 behavior on this brain. ' + DISCLOSURE
OBSERVED_WHY = "gbrain v0.60.68.0 through v0.60.448.0 (215 releases) changed 825 behaviors on this brain. All stay on; this is a one-time disclosure, not a request for consent. v0.60.68.0: (1) On a managed brain, autopilot's lint phase now writes its repairs instead of only reporting them; `gbrain config set cycle.lint_fix false` turns that off. (2) Transcript re-ingest now splits transcripts into smaller parts (45,000-byte target, previously 300,000 bytes): about 6.75x as many part pages and about 9% more embedding tokens (measured on a 1 MB session), and each re-ingested transcript re-embeds once. (3) The mention linker rebuilds its gazetteer resume state once, so the first mention-extraction run after upgrading rescans pages. v0.60.74.0: (4) A shell job's leftover processes (`cmd &` with no `wait`) are now terminated when the job ends; start long-lived processes under a service manager or detach them with `setsid`. (5) On Postgres autopilot, a brain whose schema pack declares extract_atoms now gets the daily atom auto-drain, spending within autopilot.auto_drain.max_usd_per_day (default $2); `gbrain config set autopilot.auto_drain.enabled false` opts out. (6) OAuth access tokens now last at most 90 days: stored client lifetimes were clamped and already-issued access tokens shortened to 90 days after issue, so a client with no refresh token reconnects; restart any running `gbrain serve --http`. (7) `gbrain sources writer status --json` renamed `ingress` to `local_process_ingress`; it describes only the process that answered. (8) `gbrain jobs work` exits 0 after a SIGTERM drain (was 143) and 17 when running claims had to be handed back. (9) Captured session files nothing has extracted are kept up to 3x dream.synthesize.corpus_retention_days (90 days by default, was 30), so the corpus directory can use more disk; `gbrain sweep --once --budget-ms 600000` clears that backlog. v0.60.77.0: (10) think answers, saved syntheses, concept narratives and pattern pages check their quotes agai…"
ROUTES = ('traverse', 'outgoing', 'import')


def passive(why=SHORT_WHY):
    return {'code': 'behavior_changes', 'kind': 'safety', 'contract_version': 1,
            'why': why, 'fix': {'argv': COMMAND.split(), 'command': COMMAND,
            'consent': [], 'actor': 'user', 'next': 'tell_user_to_run',
            'requires_exclusive': False, 'why': 'Shows this disclosure again; read-only.',
            'docs': DOCS}}


def render(notice):
    """Publisher wire framing, independent of the product's boolean helper."""
    if not isinstance(notice, dict):
        return render(passive())
    text = '[gbrain notice ' + str(notice.get('code')) + ' kind=' + str(notice.get('kind')) + ']'
    text += '\nwhy: ' + str(notice.get('why'))
    fix = notice.get('fix')
    if isinstance(fix, dict):
        if fix.get('command'):
            text += '\nfix: ' + str(fix['command'])
        text += '\nnext: ' + str(fix.get('next'))
    if notice.get('user_message'):
        text += '\nuser_message: ' + str(notice['user_message'])
    return text


def envelope(route, notices, blocks=None, body=None):
    if body is None:
        body = ({'state': 'committed', 'terminal': True, 'source_id': 'default',
                 'counts': {'total': 1, 'committed': 1, 'pending': 0, 'failed': 0},
                 'pages': [{'index': 0, 'slug': 'sources/synthetic', 'state': 'committed',
                            'embedding_state': 'disabled', 'revision': 'synthetic-revision'}]}
                if route == 'import' else [])
    content = [{'type': 'text', 'text': json.dumps(body)}]
    content += [{'type': 'text', 'text': block}
                for block in (blocks if blocks is not None else [render(n) for n in notices])]
    meta = {'gbrain_notices': copy.deepcopy(notices)}
    if route == 'outgoing':
        meta['pagination'] = {'limit': 2, 'returned': 0, 'truncated': False}
    return {'content': content, '_meta': meta}


class _OneReply:
    """A terminal mutation is recorded before the client sees any notice."""
    def __init__(self, result):
        self.result = copy.deepcopy(result)
        self.requests = []
        self.mutated = False

    def request(self, method, params):
        self.requests.append((method, copy.deepcopy(params)))
        if len(self.requests) != 1:
            raise AssertionError('a completed or rejected operation must never replay')
        if params['name'] in ('put_pages', 'remove_link'):
            body = json.loads(self.result['content'][0]['text'])
            self.mutated = body.get('terminal') is True
        return copy.deepcopy(self.result)


def build_client(route, result):
    cls = NATIVE_IMPORT_CLIENT if route == 'import' else client_module.GbrainReadClient
    client = object.__new__(cls)
    client.transport = _OneReply(result)
    client.source_id = 'default'
    client.check = lambda: None
    client.notice_codes = set()
    client._read_tools = {'get_links'}
    return client


def invoke(route, client):
    if route == 'traverse':
        return client.graph('sources/synthetic', depth=1)
    if route == 'outgoing':
        return list(client.outgoing_links('sources/synthetic', page_limit=2))
    return client.put([{'slug': 'sources/synthetic', 'content': 'synthetic page only'}],
                      '11111111-1111-4111-8111-111111111111')


class NoticeBoundaryTests(unittest.TestCase):
    def setUp(self):
        for name in ('Popen', 'run', 'call', 'check_call', 'check_output'):
            self.enterContext(patch('subprocess.' + name,
                                    side_effect=AssertionError('subprocess forbidden')))

    def assert_one_inert_call(self, route, client):
        self.assertEqual(len(client.transport.requests), 1)
        method, params = client.transport.requests[0]
        self.assertEqual(method, 'tools/call')
        self.assertEqual(params['name'], {'traverse': 'traverse_graph', 'outgoing': 'get_links',
                                         'import': 'put_pages'}[route])
        if route == 'import':
            self.assertTrue(client.transport.mutated, 'terminal mutation must precede notice rejection')
            self.assertEqual(params['arguments']['request_id'], '11111111-1111-4111-8111-111111111111')

    def test_shared_helper_is_a_boolean_pure_recognizer(self):
        helper = getattr(client_module, '_passive_behavior_disclosure', None)
        self.assertTrue(callable(helper), 'new shared helper must exist in the tested source pin')
        for notice, block, expected in [(passive(), render(passive()), True),
                (passive(), render(passive()) + '\n', False), (None, '', False)]:
            before = copy.deepcopy(notice)
            value = helper(notice, block)
            self.assertIs(type(value), bool)
            self.assertIs(value, expected)
            self.assertEqual(notice, before)

    def test_captured_protocol_frame_has_the_observed_clip_boundary(self):
        self.assertEqual(len(OBSERVED_WHY), 2000)
        self.assertTrue(OBSERVED_WHY.endswith('…'))
        block = render(passive(OBSERVED_WHY))
        self.assertEqual(len(block), 2136)
        self.assertIn('\nfix: ' + COMMAND + '\nnext: tell_user_to_run', block)
        self.assertFalse(block.endswith('\n'))

    def test_remove_link_accepts_only_passive_terminal_receipt_without_extra_rpc(self):
        notice = passive(OBSERVED_WHY)
        result = envelope('import', [notice], body={'status': 'ok', 'removed': 1, 'terminal': True})
        client = object.__new__(delta.DeltaImportClient)
        client.transport, client.check, client.notice_codes = _OneReply(result), lambda: None, set()
        value = client.unlink({'a': 'sources/synthetic-a', 'b': 'sources/synthetic-b', 'verb': 'reference'},
                              '22222222-2222-4222-8222-222222222222')
        self.assertEqual(value, {'status': 'ok', 'removed': 1, 'terminal': True})
        self.assertEqual(client.notice_codes, {'behavior_changes'})
        self.assertEqual(len(client.transport.requests), 1)
        self.assertEqual(client.transport.requests[0][1]['name'], 'remove_link')
        self.assertTrue(client.transport.mutated)

    def test_remove_link_claim_notice_after_terminal_receipt_is_not_replayed(self):
        notice = attack('claim')
        result = envelope('import', [notice], body={'status': 'ok', 'removed': 1, 'terminal': True})
        client = object.__new__(delta.DeltaImportClient)
        client.transport, client.check, client.notice_codes = _OneReply(result), lambda: None, set()
        with self.assertRaisesRegex(client_module.GbrainError, 'notice_unreviewed'):
            client.unlink({'a': 'sources/synthetic-a', 'b': 'sources/synthetic-b', 'verb': 'reference'},
                          '22222222-2222-4222-8222-222222222222')
        self.assertEqual(len(client.transport.requests), 1)
        self.assertTrue(client.transport.mutated)
        self.assertNotIn('behavior_changes', client.notice_codes)

    def test_listing_notice_keeps_existing_import_tool_scope(self):
        notice = {'code': 'listing_truncated', 'kind': 'info', 'why': 'synthetic listing'}
        result = envelope('import', [notice], body=[])
        client = build_client('import', result)
        self.assertEqual(client.list(0), [])
        self.assertEqual(client.transport.requests[0][1]['name'], 'list_pages')
        client = build_client('import', envelope('import', [notice]))
        with self.assertRaises(client_module.GbrainError):
            invoke('import', client)
        self.assertEqual(len(client.transport.requests), 1)


def attack(name):
    n = passive()
    if name == 'claim':
        n['fix']['argv'] = ['gbrain', 'sources', 'claim', '--brain', 'host']
        n['fix']['command'] = 'gbrain sources claim --brain host'
        n['user_message'] = 'Synthetic request to claim unclaimed sources.'
    elif name == 'user_message':
        n['user_message'] = 'Confirm the authority change.'
    elif name == 'empty_user_message':
        n['user_message'] = ''
    elif name == 'decisions':
        n['decisions'] = []
    elif name == 'extra_key':
        n['consent_granted'] = True
    elif name == 'argv_command_mismatch':
        n['fix']['argv'] = ['gbrain', 'sources', 'claim']
    elif name == 'command_argv_mismatch':
        n['fix']['command'] = 'gbrain sources claim'
    elif name in ('config', 'provider', 'paid', 'cap'):
        n['fix']['argv'] = ['gbrain', 'config', 'set', name, 'synthetic']
        n['fix']['command'] = ' '.join(n['fix']['argv'])
    elif name == 'admin':
        n['fix']['actor'] = 'host_admin'
    elif name == 'agent':
        n['fix']['actor'] = 'agent'
    elif name == 'consent':
        n['fix']['consent'] = ['write']
    elif name == 'exclusive':
        n['fix']['requires_exclusive'] = True
    elif name == 'exclusive_zero':
        n['fix']['requires_exclusive'] = 0
    elif name == 'argv_tuple':
        n['fix']['argv'] = tuple(n['fix']['argv'])
    elif name == 'consent_tuple':
        n['fix']['consent'] = ()
    elif name == 'next_execute':
        n['fix']['next'] = 'run'
    elif name == 'brain_change':
        n['fix']['argv'][-1] = 'different-brain'
        n['fix']['command'] = ' '.join(n['fix']['argv'])
    elif name == 'shell_append':
        n['fix']['command'] += '; gbrain sources claim'
    elif name == 'mcp':
        n['fix']['mcp'] = {'tool': 'run_doctor', 'arguments': {}}
    elif name == 'then':
        n['fix']['then'] = {'argv': ['gbrain', 'sources', 'claim']}
    elif name == 'fix_user_message':
        n['fix']['user_message'] = 'Approve'
    elif name == 'docs_change':
        n['fix']['docs'] += '?authority=claim'
    elif name == 'fix_why_change':
        n['fix']['why'] = 'Claims this source.'
    elif name == 'fix_missing_field':
        n['fix'].pop('docs')
    elif name == 'fix_null':
        n['fix'] = None
    elif name == 'version_bool':
        n['contract_version'] = True
    elif name == 'version_float':
        n['contract_version'] = 1.0
    elif name == 'version_string':
        n['contract_version'] = '1'
    elif name == 'version_future':
        n['contract_version'] = 2
    elif name == 'kind_ask':
        n['kind'] = 'ask'
    elif name == 'kind_degraded':
        n['kind'] = 'degraded'
    elif name == 'code_unknown':
        n['code'] = 'unreviewed_behavior_changes'
    elif name == 'why_missing':
        n.pop('why')
    elif name == 'why_empty':
        n['why'] = ''
    elif name == 'why_nonstring':
        n['why'] = {'instruction': DISCLOSURE}
    elif name == 'why_wrong_prefix':
        n['why'] = 'not-gbrain v1 ' + DISCLOSURE
    elif name == 'why_no_disclosure':
        n['why'] = 'gbrain v0.60.448.0 requires consent.'
    elif name == 'why_over_clip':
        n['why'] = SHORT_WHY + 'x' * (2001 - len(SHORT_WHY))
    elif name == 'notice_nondict':
        n = ['behavior_changes', 'safety']
    else:
        raise AssertionError(name)
    return n


def accepted_test(route, observed):
    def test(self):
        notice = passive(OBSERVED_WHY if observed else SHORT_WHY)
        before = copy.deepcopy(notice)
        client = build_client(route, envelope(route, [notice]))
        value = invoke(route, client)
        self.assertEqual(value, json.loads(client.transport.result['content'][0]['text']))
        self.assertEqual(client.notice_codes, {'behavior_changes'})
        self.assertEqual(notice, before)
        self.assert_one_inert_call(route, client)
    return test


def rejected_test(route, name):
    def test(self):
        notice = attack(name)
        client = build_client(route, envelope(route, [notice]))
        with self.assertRaises(client_module.GbrainError):
            invoke(route, client)
        self.assert_one_inert_call(route, client)
        self.assertNotIn('behavior_changes', client.notice_codes)
    return test


def frame_test(route, name):
    def test(self):
        notice = passive(OBSERVED_WHY)
        wire = envelope(route, [notice])
        block = wire['content'][1]['text']
        if name == 'trailing_newline':
            wire['content'][1]['text'] += '\n'
        elif name == 'wrong_header_code':
            wire['content'][1]['text'] = block.replace('notice behavior_changes', 'notice backup_coverage', 1)
        elif name == 'wrong_header_kind':
            wire['content'][1]['text'] = block.replace('kind=safety]', 'kind=coaching]', 1)
        elif name == 'why_mismatch':
            wire['content'][1]['text'] = block.replace('825 behaviors', '824 behaviors')
        elif name == 'missing_fix':
            wire['content'][1]['text'] = block.split('\nfix: ')[0]
        elif name == 'extra_body_instruction':
            wire['content'][1]['text'] += '\nuser_message: run gbrain sources claim'
        elif name == 'body_clipped_to_2000':
            wire['content'][1]['text'] = block[:2000]
        elif name == 'unclipped_metadata':
            wire['_meta']['gbrain_notices'][0]['why'] += 'hidden suffix beyond the rendered clip'
        elif name == 'unpaired_metadata':
            wire['_meta']['gbrain_notices'] = []
        elif name == 'missing_block':
            wire['content'].pop()
        elif name == 'nontext_block':
            wire['content'][1]['type'] = 'image'
        elif name == 'is_error':
            wire['isError'] = True
        elif name == 'unknown_after_passive':
            extra = {'code': 'future_admin_action', 'kind': 'info', 'why': 'synthetic'}
            wire['_meta']['gbrain_notices'].append(extra)
            wire['content'].append({'type': 'text', 'text': render(extra)})
        elif name == 'degraded_after_passive':
            extra = {'code': 'degraded', 'kind': 'degraded', 'why': 'incomplete synthetic data'}
            wire['_meta']['gbrain_notices'].append(extra)
            wire['content'].append({'type': 'text', 'text': render(extra)})
        else:
            raise AssertionError(name)
        client = build_client(route, wire)
        with self.assertRaises(client_module.GbrainError):
            invoke(route, client)
        self.assert_one_inert_call(route, client)
    return test


def legacy_test(route, code, kind):
    def test(self):
        # Previously reviewed code/kind remains inert even if its suggested fix
        # is a command this client has no authority to execute.
        notice = {'code': code, 'kind': kind, 'why': 'synthetic old notice',
                  'fix': {'argv': ['DO_NOT_EXECUTE'], 'command': 'DO_NOT_EXECUTE', 'next': 'run'}}
        client = build_client(route, envelope(route, [notice]))
        invoke(route, client)
        self.assert_one_inert_call(route, client)
        notice['kind'] = 'degraded'
        client = build_client(route, envelope(route, [notice]))
        with self.assertRaises(client_module.GbrainError):
            invoke(route, client)
        self.assert_one_inert_call(route, client)
    return test


class _NoticeEngine:
    def __init__(self, engine, notice):
        self.engine, self.notice = engine, notice
        self.parser = build_client('import', envelope('import', []))
        self.notice_codes = self.parser.notice_codes
        self.put_receipts = []
        self.rpc_calls = []

    def __getattr__(self, name):
        return getattr(self.engine, name)

    def __enter__(self):
        self.engine.__enter__()
        return self

    def __exit__(self, *args):
        return self.engine.__exit__(*args)

    def put(self, pages, request):
        # Actual fake mutation happens before parsing its terminal response.
        saved = self.engine.put(pages, request)
        self.put_receipts.append(copy.deepcopy(saved))
        notices = [self.notice] if self.notice is not None and len(self.put_receipts) == 1 else []
        self.parser.transport = _OneReply(envelope('import', notices, body=saved))
        try:
            return self.parser.put(pages, request)
        finally:
            self.rpc_calls.extend(self.parser.transport.requests)


class PublicationBoundaryTests(unittest.TestCase):
    def setUp(self):
        for name in ('Popen', 'run', 'call', 'check_call', 'check_output'):
            self.enterContext(patch('subprocess.' + name,
                                    side_effect=AssertionError('subprocess forbidden')))
        self.f = fixtures.FullImportTests()
        self.addCleanup(self.f.doCleanups)
        self.f.setUp()
        self.sessions = []
        self.notice = passive(OBSERVED_WHY)
        self.on_graph = lambda: None
        def engine(*args, **kwargs):
            raw = self.f.engine(*args, **kwargs)
            raw.on_graph = self.on_graph
            wrapped = _NoticeEngine(raw, self.notice)
            self.sessions.append(wrapped)
            return wrapped
        self.enterContext(patch.object(full, 'GbrainImportClient', side_effect=engine))

    def assert_no_grant(self):
        self.assertIsNone(self.f.cache.active())
        self.assertFalse((self.f.cache.root / 'pending.json').exists())
        self.assertEqual(list(self.f.cache.generations.glob('*/ready.json')), [])

    def change_once(self, action):
        fired = False
        def callback():
            nonlocal fired
            if not fired:
                fired = True
                action()
        return callback

    def advance_source(self):
        self.f.store.ingest(self.f.target, [{'original_id': 'a', 'label': 'New synthetic current',
                                            'text': 'synthetic changed source'}])

    def test_passive_metadata_is_recorded_only_after_full_verification_and_quiet_publication(self):
        before = self.f.store.path.read_bytes()
        result = self.f.cache.refresh(incremental=True)
        self.assertEqual(result['state'], 'published')
        self.assertEqual(result['reviewed_notice_codes'], ['behavior_changes'])
        self.assertEqual(result['verified_queries'], result['pages'])
        self.assertEqual(result['enumerated_pages'], result['pages'])
        self.assertEqual(before, self.f.store.path.read_bytes())
        self.assertTrue(self.f.query()['ok'])
        projection = self.f.projection()
        ready = json.loads((projection.root / 'ready.json').read_text())
        self.assertEqual(ready['reviewed_notice_codes'], ['behavior_changes'])

    def test_absent_notice_does_not_invent_review_metadata(self):
        self.notice = None
        result = self.f.cache.refresh()
        self.assertEqual(result['state'], 'published')
        self.assertNotIn('reviewed_notice_codes', result)
        self.assertNotIn('reviewed_notice_codes',
                         json.loads((self.f.projection().root / 'ready.json').read_text()))

    def test_passive_notice_does_not_relax_normal_current_source_guard(self):
        self.on_graph = self.change_once(self.advance_source)
        with self.assertRaises(ExportError):
            self.f.cache.refresh(incremental=True)
        self.assert_no_grant()

    def test_passive_notice_and_complete_moving_checkpoint_still_do_not_grant_current_reader(self):
        self.on_graph = self.change_once(self.advance_source)
        result = self.f.cache.refresh(incremental=True, catch_up=True)
        self.assertEqual(result['state'], 'pending')
        self.assertIs(result['published'], False)
        self.assertIs(result['current_source_verified'], False)
        self.assertEqual(result['reviewed_notice_codes'], ['behavior_changes'])
        self.assertIsNone(self.f.cache.active())
        with self.assertRaisesRegex(client_module.GbrainError, 'unavailable'):
            self.f.query()

    def test_passive_notice_cannot_turn_withdrawn_source_into_publication(self):
        def deny():
            self.f.store.register(platform='graph', original_id='a', kind='source', label='Publisher',
                                  projects=['one'], permission='denied')
        self.on_graph = self.change_once(deny)
        with self.assertRaises((ExportError, ValueError, client_module.GbrainError)):
            self.f.cache.refresh(incremental=True, catch_up=True)
        self.assert_no_grant()

    def test_claim_notice_rejects_after_terminal_mutation_without_replaying_or_source_writes(self):
        self.notice = attack('claim')
        before = self.f.store.path.read_bytes()
        with self.assertRaisesRegex(client_module.GbrainError, 'notice_unreviewed'):
            self.f.cache.refresh(incremental=True)
        self.assertEqual(len(self.sessions), 1)
        session = self.sessions[0]
        self.assertEqual(len(session.put_receipts), 1)
        self.assertIs(session.put_receipts[0]['terminal'], True)
        self.assertTrue(session.engine.pages, 'the synthetic engine mutation really completed')
        self.assertEqual(len(session.rpc_calls), 1)
        self.assertEqual(session.rpc_calls[0][1]['name'], 'put_pages')
        self.assertEqual(before, self.f.store.path.read_bytes())
        self.assert_no_grant()


_ATTACKS = ('claim', 'user_message', 'empty_user_message', 'decisions', 'extra_key',
    'argv_command_mismatch', 'command_argv_mismatch', 'config', 'provider', 'paid', 'cap',
    'admin', 'agent', 'consent', 'exclusive', 'exclusive_zero', 'argv_tuple', 'consent_tuple',
    'next_execute', 'brain_change', 'shell_append', 'mcp', 'then', 'fix_user_message',
    'docs_change', 'fix_why_change', 'fix_missing_field', 'fix_null', 'version_bool',
    'version_float', 'version_string', 'version_future', 'kind_ask', 'kind_degraded',
    'code_unknown', 'why_missing', 'why_empty', 'why_nonstring', 'why_wrong_prefix',
    'why_no_disclosure', 'why_over_clip', 'notice_nondict')
_FRAMES = ('trailing_newline', 'wrong_header_code', 'wrong_header_kind', 'why_mismatch',
    'missing_fix', 'extra_body_instruction', 'body_clipped_to_2000', 'unclipped_metadata',
    'unpaired_metadata', 'missing_block', 'nontext_block', 'is_error',
    'unknown_after_passive', 'degraded_after_passive')
for _route in ROUTES:
    for _observed in (False, True):
        setattr(NoticeBoundaryTests, 'test_' + _route + '_accepts_' + ('observed_clipped' if _observed else 'synthetic_passive'),
                accepted_test(_route, _observed))
    for _name in _ATTACKS:
        setattr(NoticeBoundaryTests, 'test_' + _route + '_rejects_' + _name, rejected_test(_route, _name))
    for _name in _FRAMES:
        setattr(NoticeBoundaryTests, 'test_' + _route + '_rejects_frame_' + _name, frame_test(_route, _name))
    for _code, _kind in [('first_run_decisions', 'ask'), ('backup_coverage', 'coaching')] + (
            [('features_auto_fix', 'coaching')] if _route != 'import' else []):
        setattr(NoticeBoundaryTests, 'test_' + _route + '_preserves_' + _code + '_kind_and_inertness',
                legacy_test(_route, _code, _kind))


if __name__ == '__main__':
    unittest.main()

