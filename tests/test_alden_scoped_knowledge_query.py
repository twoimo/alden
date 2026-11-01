"""Closed collection queries over generated retained originals; HTTP is mocked."""
from contextlib import contextmanager
import copy
import hashlib
import io
import json
from pathlib import Path
import sys
import unicodedata
import unittest
from unittest.mock import patch
import uuid

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from tests import test_alden_original_search_integration as integration
import alden_collection_retrieval as retrieval
import alden_scoped_knowledge_query as scoped
import alden_voice as voice
import auto_reply_knowledge_graph as kg


def digest(text):
    return hashlib.sha256(unicodedata.normalize('NFC', text).strip().encode()).hexdigest()


def expected_trace(request, query):
    return {'policy': 'explicit-collection-query-v1',
            'derived': digest(request) != digest(query),
            'request_sha256': digest(request), 'retrieval_sha256': digest(query)}


def context_from(reference):
    return json.loads(reference.split('<quoted_local_history>', 1)[1]
                      .split('</quoted_local_history>', 1)[0])


class ScopedKnowledgeQueryTests(unittest.TestCase):
    PROJECT = 'PROJECT'

    def setUp(self):
        # Compose the accepted real parser/temp/SQLite/network/model guards.
        self.fixture = integration.OriginalSearchIntegrationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.addCleanup(self.fixture.tearDown)

    def corpus(self, name='scoped'):
        secret = 'selected:' + uuid.uuid4().hex
        primary = (b'neutral retained text ' * 1100 +
                   ('\r\ntailneedle ' + secret + '\r\n').encode())
        distractor = ((self.PROJECT + ' 저장된 자료 원본 SHA256 확인해줘\r\n') * 24).encode()
        state = self.fixture.build(name=name, project=self.PROJECT,
            documents={'primary': primary, 'distractor': distractor})
        self.assertGreater(primary.index(b'tailneedle'), 16000)
        receipt = state['captured']['original_text_index']
        self.assertEqual(receipt['state'], 'ready')
        self.assertEqual(receipt['indexed'] + receipt['reused'], 2)
        self.assertEqual(receipt['embeddings_generated'], 0)
        state['secret'] = secret
        state['request'] = self.PROJECT + ' 저장된 자료에서 tailneedle 원본 SHA256을 확인해줘'
        # Reads must use retained fixture bytes even after original paths vanish.
        for path in state['paths'].values():
            path.unlink()
        return state

    def assert_original(self, state, provenance, query='tailneedle'):
        self.assertEqual(provenance['entity_id'], state['ids']['primary'])
        self.assertEqual({row['target_id'] for row in provenance['source_memberships']}, {state['target']})
        self.assertEqual(provenance['source_version'], state['versions'][state['ids']['primary']])
        self.assertEqual(provenance['requested_projects'], [state['project']])
        self.assertEqual(provenance['body_source'], 'verified_retained_original_projection')
        self.assertEqual(provenance['original_body_coverage']['status'], 'partial')
        self.assertFalse(provenance['original_body_coverage']['body_complete'])
        proof = provenance['original_text_excerpt']
        self.assertEqual(proof['binding']['target_id'], state['target'])
        self.assertEqual(proof['binding']['version'], provenance['source_version'])
        self.assertEqual(proof['selection']['query_sha256'], digest(query))
        body = integration.normalized_body(state['raw']['primary'].decode())
        coords = proof['excerpt']
        text = body[coords['start_character']:coords['end_character']]
        self.assertIn('tailneedle', text)
        self.assertIn(state['secret'], text)
        self.fixture.assert_proof(state, 'primary', proof, text)
        return proof

    def test_closed_grammar_preserves_payload_and_strips_only_terminal_output(self):
        cases = []
        for carrier in ('저장된 자료에서', '수집된 파일 중에서', '보관된 문서중',
                        '저장된 원문의', '수집된 노트에서'):
            for prefix in ('PROJECT ', '프로젝트 PROJECT ', 'PROJECT 프로젝트 '):
                for ending in ('찾아줘', '검색해줘요!', '확인해줘?'):
                    cases.append((prefix + carrier + ' tailneedle 원본 SHA256을 ' + ending,
                                  'tailneedle'))
        cases += [(' PROJECT 저장된 자료에서 café 해시를 확인해줘요. ', 'café'),
                  ('PROJECT 저장된 자료에서 원본 SHA256 설명 찾아줘', '원본 SHA256 설명'),
                  ('PROJECT 저장된 자료에서 sha-256 검색해줘', None),
                  ('PROJECT 저장된 자료에서 원본 해시 확인해줘', None),
                  ('PROJECT 저장된 자료에서 tailneedle 해시를 찾아줘', 'tailneedle'),
                  ('PROJECT 저장된 자료에서 tailneedle 찾아줘', 'tailneedle')]
        for request, query in cases:
            with self.subTest(request=request):
                query = unicodedata.normalize('NFC', request).strip() if query is None else query
                result = scoped.scoped_collection_query(request, projects=[self.PROJECT])
                self.assertEqual(set(result), {'query', 'trace'})
                self.assertEqual(result, {'query': query, 'trace': expected_trace(request, query)})

    def test_unknown_or_incomplete_wrappers_fall_back_without_broadening(self):
        requests = [
            'tailneedle', 'OTHER 저장된 자료에서 tailneedle 찾아줘',
            'XPROJECT 저장된 자료에서 tailneedle 찾아줘',
            'PROJECTX 저장된 자료에서 tailneedle 찾아줘',
            '먼저 PROJECT 저장된 자료에서 tailneedle 찾아줘',
            'PROJECT 자료에서 tailneedle 찾아줘',
            'PROJECT 저장된 자료 tailneedle 찾아줘',
            'PROJECT 저장된 자료에서 tailneedle 원본 SHA256',
            'PROJECT 저장된 자료에서 tailneedle 확인해줘 추가',
            'PROJECT 저장된 자료에서 tailneedle 확인해줘!!',
            'PROJECT 저장된 자료에서 원본 SHA256을 확인해줘',
            'PROJECT 저장된 자료에서 확인해줘',
            'PROJECT 저장된 자료에서\ntailneedle 찾아줘',
            'PROJECT 저장된 자료에서 tailneedle\r확인해줘',
            'PROJECT 저장된 자료에서 tailneedle 검색하지 마',
            'PROJECT 저장된 자료에서 tailneedle 저장해줘',
        ]
        for request in requests:
            with self.subTest(request=request):
                self.assertEqual(scoped.scoped_collection_query(request, projects=[self.PROJECT]),
                    {'query': request, 'trace': expected_trace(request, request)})
        request = ' PROJECT 저장된 자료에서 cafe\u0301 확인해줘 '
        normalized = unicodedata.normalize('NFC', request).strip()
        self.assertEqual(scoped.scoped_collection_query(request, projects=[])['query'], normalized)

    def test_unicode_literal_longest_project_and_case_ambiguity(self):
        cases = [
            ('카페 프로젝트 저장된 자료에서 꼬리표 원본 해시를 찾아줘',
             ['카페', '카페 프로젝트'], '꼬리표'),
            ('카페 저장된 자료에서 확장 저장된 자료에서 꼬리표 찾아줘',
             ['카페', '카페 저장된 자료에서 확장'], '꼬리표'),
            ('프로젝트 Cafe\u0301😀 저장된 자료에서 cafe\u0301tail 확인해줘',
             ['Café😀', 'Cafe\u0301😀'], 'cafétail'),
            ('A+B[1] 저장된 자료에서 가나다 찾아줘', ['A+B[1]', 'A.B1'], '가나다'),
            ('pRoJeCt 저장된 자료에서 tailneedle 찾아줘', ['PROJECT', 'Project'], None),
            ('PROJECT 저장된 자료에서 tailneedle 찾아줘', ['PROJECT', 'PROJECT'], 'tailneedle'),
        ]
        for request, projects, query in cases:
            with self.subTest(projects=projects):
                expected = unicodedata.normalize('NFC', request).strip() if query is None else query
                result = scoped.scoped_collection_query(request, projects=projects)
                self.assertEqual(result, {'query': expected, 'trace': expected_trace(request, expected)})
        first = scoped.scoped_collection_query('PROJECT 저장된 자료에서 tailneedle 찾아줘', projects=['PROJECT'])
        second = scoped.scoped_collection_query('PROJECT 저장된 자료에서 tailneedle 찾아줘', projects=['PROJECT'])
        first['trace']['policy'] = 'changed'
        self.assertEqual(second['trace']['policy'], scoped.POLICY)
        self.assertIsNot(first, second)
        self.assertIsNot(first['trace'], second['trace'])

    def test_input_contract_name_filter_and_first_128_bound(self):
        self.assertEqual(scoped.MAX_QUERY_CHARS, 4096)
        for query in (None, False, 5, [], '', ' \t\n', 'x' * 4097):
            with self.subTest(query_type=type(query).__name__, size=len(query) if isinstance(query, str) else None):
                with self.assertRaisesRegex(ValueError, '^knowledge_query_invalid$'):
                    scoped.scoped_collection_query(query, projects=[])
        for projects in (None, 'PROJECT', {}, {'PROJECT'}, iter(['PROJECT'])):
            with self.subTest(container=type(projects).__name__):
                with self.assertRaisesRegex(ValueError, '^knowledge_query_invalid$'):
                    scoped.scoped_collection_query('tailneedle', projects=projects)
        request = 'PROJECT 저장된 자료에서 tailneedle 찾아줘'
        for projects, expected in (([None, 4, '', ' ', 'x' * 1025, ['PROJECT'], 'PROJECT'], 'tailneedle'),
                                   ((self.PROJECT,), 'tailneedle'),
                                   ([None] * 127 + [self.PROJECT], 'tailneedle'),
                                   ([None] * 128 + [self.PROJECT], request)):
            with self.subTest(names=len(projects)):
                self.assertEqual(scoped.scoped_collection_query(request, projects=projects)['query'], expected)
        self.assertEqual(scoped.scoped_collection_query('x' * 4096, projects=[])['query'], 'x' * 4096)

    def test_legacy_pre_nfc_intent_bound_preserves_expanded_fallback(self):
        # U+0344 expands under NFC. Intent still cuts raw codepoints at 1024.
        request = '자료' + '\u0344' * 1022 + 'discarded-after-legacy-bound'
        admitted = voice._knowledge_request(request, [])
        self.assertEqual(admitted, unicodedata.normalize('NFC', request[:1024]))
        self.assertEqual(len(admitted), 2046)
        self.assertNotIn('discarded', admitted)
        normalized = unicodedata.normalize('NFC', '\u0344' * 1024)
        self.assertEqual(len(normalized), 2048)
        for query in (admitted, normalized):
            with self.subTest(codepoints=len(query)):
                self.assertEqual(scoped.scoped_collection_query(query, projects=[self.PROJECT]),
                    {'query': query, 'trace': expected_trace(query, query)})

    def test_real_collect_capture_both_strategies_choose_unique_original_tail(self):
        state = self.corpus('two-strategies')
        prepared = scoped.scoped_collection_query(state['request'], projects=[self.PROJECT])
        for strategy in self.fixture.STRATEGIES:
            with self.subTest(strategy=strategy):
                result = retrieval.retrieve(state['root'], prepared['query'], projects=[self.PROJECT],
                    search_strategy=strategy, candidate_limit=1, max_entities=1, max_relations=0)
                self.assertEqual(len(result['facts']), 1)
                self.assert_original(state, result['fact_provenance'][0])
                self.assertIn(state['secret'], result['facts'][0])
                self.assertEqual(result['search_mode'], 'bm25_only')

    def test_voice_reference_trace_scope_and_proof_bind_derived_query(self):
        from alden_abort import AbortController
        state = self.corpus('reference-scope')
        self.fixture.build(name='outside', root=state['root'], project='OTHER',
            documents={'primary': b'tailneedle outside-scope secret'})
        token = AbortController(state['root']).token()
        observed = []
        with patch.object(kg, 'retrieve_knowledge_bundle', wraps=kg.retrieve_knowledge_bundle) as lookup:
            reference, metrics = voice._voice_knowledge_reference(state['request'], [], state['root'], token,
                authority_observer=observed.append)
        self.assertEqual(metrics['state'], 'found')
        self.assertEqual(lookup.call_args.args[0], 'tailneedle')
        self.assertEqual(lookup.call_args.kwargs['projects'], [self.PROJECT])
        self.assertEqual(lookup.call_args.kwargs['max_original_excerpt_chars'], 400)
        context = context_from(reference)
        trace = expected_trace(state['request'], 'tailneedle')
        self.assertEqual(metrics['query_trace'], trace)
        self.assertEqual(context['query_trace'], trace)
        self.assertEqual(observed[0]['query_trace'], trace)
        self.assertEqual(len(context['graph_facts']), 1)
        self.assertIn(state['secret'], context['graph_facts'][0])
        self.assert_original(state, context['graph_provenance'][0])
        self.assertNotIn('outside-scope', reference)
        # A denied explicit project may not fall through to another visible one.
        self.fixture.mutate(state, 'revoke')
        reference, metrics = voice._voice_knowledge_reference(state['request'], [], state['root'], token,
            authority_observer=lambda context: self.fail('denied source authority'))
        self.assertEqual(metrics['state'], 'empty')
        self.assertEqual(reference, '')
        self.assertEqual(metrics['query_trace'], trace)

    def test_denied_intent_stops_before_reads_and_messenger_query_is_unchanged(self):
        from alden_abort import AbortController
        state = self.corpus('intent-boundary')
        token = AbortController(state['root']).token()
        history = [{'role': 'user', 'content': state['request']}]
        history_before = copy.deepcopy(history)
        for text in ('PROJECT 저장된 자료에서 tailneedle 검색하지 마',
                     'PROJECT 저장된 자료에서 tailneedle 검색하지 마 확인해줘',
                     'PROJECT 저장된 자료에서 tailneedle 읽지 마',
                     '이 말은 기억해줘', '오늘 날씨 좋네'):
            with self.subTest(text=text), \
                 patch.object(voice, '_voice_collection_projects', side_effect=AssertionError('scope read denied')), \
                 patch.object(kg, 'retrieve_knowledge_bundle', side_effect=AssertionError('lookup denied')), \
                 patch.object(scoped, 'scoped_collection_query', side_effect=AssertionError('preparation denied')):
                reference, metrics = voice._voice_knowledge_reference(text, history, state['root'], token)
                self.assertEqual((reference, metrics['state']), ('', 'not_requested'))
        self.assertEqual(history, history_before)
        request = 'PROJECT 카톡 저장된 자료에서 tailneedle 원본 SHA256을 찾아줘'
        with patch.object(voice, '_voice_collection_projects', side_effect=AssertionError('messenger scope')), \
             patch('alden_corpus.search', side_effect=AssertionError('no messenger fixture')), \
             patch.object(kg, 'retrieve_knowledge_bundle', return_value={'facts': [], 'fact_provenance': []}) as lookup:
            reference, metrics = voice._voice_knowledge_reference(request, [], state['root'], token)
        self.assertEqual(reference, '')
        self.assertEqual(lookup.call_args.args[0], request)
        self.assertNotIn('projects', lookup.call_args.kwargs)
        self.assertEqual(metrics['query_trace'], expected_trace(request, request))

    def test_typed_mock_preserves_request_and_persists_target_proof_and_trace(self):
        import alden_chat as chat
        from alden_history import database
        from auto_reply_ondevice import QWEN38_27B_MODEL_ID
        state = self.corpus('typed-natural')
        value = {'request_id': uuid.uuid4().hex, 'conversation_id': None,
                 'text': state['request'], 'images': []}
        original_value = copy.deepcopy(value)
        token = chat.InputToken(state['root'], str(uuid.uuid4()))
        payloads, contexts = [], []
        model = QWEN38_27B_MODEL_ID.removeprefix('mlx/')

        class Response(io.BytesIO):
            def __init__(self, body):
                super().__init__(json.dumps(body, ensure_ascii=False).encode())
                self.headers = {'Content-Type': 'application/json'}

        def fake_http(request, *, timeout):
            payload = json.loads(request.data)
            payloads.append(payload)
            self.assertFalse(payload['stream'])
            self.assertTrue(request.full_url.endswith('/chat/completions'))
            self.assertEqual([m for m in payload['messages'] if m['role'] == 'user'],
                             [{'role': 'user', 'content': state['request']}])
            self.assertEqual(payload['tools'][0]['function']['parameters']['properties'], {})
            if len(payloads) == 1:
                self.assertNotIn(state['secret'], json.dumps(payload))
                message = {'role': 'assistant', 'content': None, 'tool_calls': [{
                    'id': 'scoped-read', 'type': 'function', 'function': {
                        'name': 'lookup_requested_knowledge', 'arguments': '{}'}}]}
                finish = 'tool_calls'
            else:
                self.assertEqual(payload['messages'][:-2], payloads[0]['messages'])
                self.assertEqual(payload['messages'][-1]['role'], 'tool')
                self.assertEqual(payload['messages'][-1]['tool_call_id'], 'scoped-read')
                content = json.loads(payload['messages'][-1]['content'])
                self.assertEqual(content['state'], 'found')
                context = context_from(content['reference'])
                contexts.append(context)
                # The fake decoder copies the first source's real retained hash.
                # It does not know the expected primary hash in advance.
                proof = context['graph_provenance'][0]['original_text_excerpt']
                message = {'role': 'assistant', 'content': proof['binding']['input_sha256']}
                finish = 'stop'
            return Response({'id': 'scoped-response-' + str(len(payloads)), 'model': model,
                'choices': [{'index': 0, 'finish_reason': finish, 'message': message}],
                'usage': {'prompt_tokens': 17, 'completion_tokens': 9}})

        @contextmanager
        def fake_lease(root):
            self.assertEqual(Path(root), state['root'])
            yield

        with patch.object(voice, '_local_urlopen', side_effect=fake_http), \
             patch.object(voice.LocalMlxLlm, '_require_selected_model_ready', return_value=None), \
             patch('local_mlx_gateway.mlx_model_request_lease', side_effect=fake_lease):
            result = chat.process_input(state['root'], value, token,
                select_model=lambda _root: {'transport': 'local', 'model': QWEN38_27B_MODEL_ID,
                    'base_url': 'http://127.0.0.1:11234/v1', 'reasoning_effort': None})
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['state'], 'completed')
        self.assertEqual(value, original_value)
        self.assertEqual(len(payloads), 2)
        self.assertEqual(result['reply'], integration.sha(state['raw']['primary']))
        self.assert_original(state, contexts[0]['graph_provenance'][0])
        trace = expected_trace(state['request'], 'tailneedle')
        self.assertEqual(contexts[0]['query_trace'], trace)
        self.assertEqual(result['generation']['knowledge_query'], trace)
        self.assertEqual(result['generation']['tool_loop']['read_count'], 1)
        with database(state['root']) as db:
            saved = db.execute('SELECT provenance FROM voice_sources WHERE session_id=? AND turn_id=?',
                (result['conversation_id'], result['turn_id'])).fetchone()
            durable = json.loads(db.execute('SELECT metadata_json FROM alden_chat_generations WHERE request_id=?',
                (value['request_id'],)).fetchone()[0])
            user = db.execute('SELECT content FROM voice_messages WHERE session_id=? AND turn_id=? AND role=?',
                (result['conversation_id'], result['turn_id'], 'user')).fetchone()[0]
        self.assertEqual(user, state['request'])
        self.assertEqual(durable, result['generation'])
        self.assertEqual(durable['knowledge_query'], trace)
        self.assertNotIn('tailneedle', json.dumps(durable))
        self.assertNotIn(self.PROJECT, json.dumps(durable))
        self.assert_original(state, json.loads(saved[0])[0])

    def test_durable_generation_trace_is_fresh_exact_and_malformed_is_rejected(self):
        from alden_chat_metrics import sanitize_generation
        ticket = {'conversation_id': 'a' * 32, 'turn_id': 1, 'context_version': 1}
        valid = expected_trace('PROJECT 저장된 자료에서 tailneedle 찾아줘', 'tailneedle')
        metrics = {'retrieval': {'query_trace': copy.deepcopy(valid)}}
        result = sanitize_generation(None, metrics, ticket)
        self.assertEqual(result['knowledge_query'], valid)
        self.assertIsNot(result['knowledge_query'], metrics['retrieval']['query_trace'])
        metrics['retrieval']['query_trace']['policy'] = 'mutated'
        self.assertEqual(result['knowledge_query'], valid)
        unchanged = expected_trace('unchanged', 'unchanged')
        self.assertEqual(sanitize_generation(None, {'retrieval': {'query_trace': unchanged}}, ticket)
                         ['knowledge_query'], unchanged)
        bad = [None, [], {}, 'private', {**valid, 'query': 'private'},
               {**valid, 'policy': 'unknown'}, {**valid, 'derived': 1},
               {**valid, 'derived': 'true'}, {**valid, 'derived': False},
               {**unchanged, 'derived': True}]
        bad += [{key: value for key, value in valid.items() if key != missing} for missing in valid]
        for key in ('request_sha256', 'retrieval_sha256'):
            bad += [{**valid, key: value} for value in (None, 1, 'A' * 64, 'a' * 63, 'g' * 64, 'a' * 64 + '\n')]
        for trace in bad:
            with self.subTest(trace=trace):
                with self.assertRaisesRegex(ValueError, '^generation_metadata_invalid$'):
                    sanitize_generation(None, {'retrieval': {'query_trace': trace}}, ticket)
        self.assertNotIn('knowledge_query', sanitize_generation(None, {}, ticket))


if __name__ == '__main__':
    unittest.main()
