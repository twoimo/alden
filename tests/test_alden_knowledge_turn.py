"""Typed product path, mocked model transport, real collection execution.

The answer nonce is absent from the input/history/filename. The mock final
model can recover it only from the actual tool-role response. This is plumbing
evidence, not an actual local decoder choice or installed UI test.
"""
from contextlib import contextmanager
import copy
import io
import json
from pathlib import Path
import re
import socket
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import alden_chat as chat
import alden_voice as voice
import alden_knowledge_turn as loop
from alden_abort import AbortController, AldenCancelled
from alden_collection import CollectionStore
from auto_reply_ondevice import QWEN38_27B_MODEL_ID

MODEL = QWEN38_27B_MODEL_ID.removeprefix('mlx/')


def completion(kind='tool', *, identifier='reply-1', text='완료'):
    message = {'role': 'assistant', 'content': text}
    reason = 'stop'
    if kind == 'tool':
        message = {'role': 'assistant', 'content': None, 'tool_calls': [{
            'id': 'read-1', 'type': 'function', 'function': {
                'name': 'lookup_requested_knowledge', 'arguments': '{}'}}]}
        reason = 'tool_calls'
    return {'id': identifier, 'model': MODEL, 'choices': [
        {'index': 0, 'finish_reason': reason, 'message': message}],
        'usage': {'prompt_tokens': 17, 'completion_tokens': 9}}


class Response(io.BytesIO):
    def __init__(self, body, mime='application/json'):
        super().__init__(body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode())
        self.headers = {'Content-Type': mime}


class KnowledgeTurnTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory(prefix='alden-knowledge-turn-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.store = CollectionStore(self.root)
        self.registration = dict(platform='graph', original_id='private-source', kind='source',
                                 label='기밀분야 지식 그래프', projects=['tool-project'])
        self.target = self.store.register(**self.registration)
        self.nonce = 'nonce:' + uuid.uuid4().hex
        self.store.ingest(self.target, [{'original_id': 'record-1', 'label': 'scopeprobe source',
                                        'text': 'scopeprobe ' + self.nonce}])
        self.value = {'request_id': uuid.uuid4().hex, 'conversation_id': None,
                      'text': 'tool-project 지식에서 scopeprobe 자료를 찾아줘', 'images': []}
        self.token = chat.InputToken(self.root, str(uuid.uuid4()))
        self.payloads, self.timeouts, self.reads, self.leases = [], [], 0, 0
        self.behavior = None
        self.fake_mime = 'application/json'
        self.patches = [
            patch.object(socket.socket, 'connect', side_effect=AssertionError('live network forbidden')),
            patch('alden_voice._local_urlopen', side_effect=self.urlopen),
            patch.object(voice.LocalMlxLlm, '_require_selected_model_ready', return_value=None),
            patch('local_mlx_gateway.mlx_model_request_lease', side_effect=self.lease),
            patch('auto_reply_knowledge_graph._local_dense_embeddings', return_value=None),
            patch('auto_reply_knowledge_graph._active_dense_embedding_model', return_value='fixture-encoder'),
            patch('auto_reply_knowledge_graph._dense_endpoint_identity', return_value='fixture://offline'),
        ]
        from alden_tool_runtime import AldenToolRuntime
        original = AldenToolRuntime.run_requested_knowledge
        test = self
        def read(runtime, *args, **kwargs):
            test.reads += 1
            return original(runtime, *args, **kwargs)
        self.patches.append(patch.object(AldenToolRuntime, 'run_requested_knowledge', read))
        for item in self.patches:
            item.start(); self.addCleanup(item.stop)

    @contextmanager
    def lease(self, root):
        self.assertEqual(Path(root), self.root)
        self.leases += 1
        yield

    def urlopen(self, request, *, timeout):
        payload = json.loads(request.data)
        self.payloads.append(payload); self.timeouts.append(timeout)
        self.assertFalse(payload['stream'])
        self.assertGreater(timeout, 0); self.assertLessEqual(timeout, loop.TOTAL_SECONDS)
        if self.behavior is not None:
            value = self.behavior(payload, len(self.payloads))
            if value is not None: return Response(value, self.fake_mime)
        if len(self.payloads) == 1:
            self.assertNotIn(self.nonce, json.dumps(payload, ensure_ascii=False))
            return Response(completion(), self.fake_mime)
        self.assertEqual([m['role'] for m in payload['messages'][-3:]], ['user','assistant','tool'])
        self.assertEqual(payload['messages'][-1]['tool_call_id'], 'read-1')
        content = payload['messages'][-1]['content']
        found = re.search(r'nonce:[0-9a-f]{32}', content)
        answer = found.group() if found else '허용된 자료가 없습니다'
        return Response(completion('final', identifier='reply-2', text=answer), self.fake_mime)

    def run_input(self, value=None, **kwargs):
        return chat.process_input(self.root, value or self.value, self.token,
            select_model=lambda _root: {'transport':'local', 'model':QWEN38_27B_MODEL_ID,
                'base_url':'http://127.0.0.1:11234/v1', 'reasoning_effort':None}, **kwargs)

    def assert_failed(self, result, code=None, *, posts=None, reads=None):
        self.assertFalse(result['ok'], result)
        self.assertNotEqual(result['state'], 'completed')
        if code: self.assertEqual(result['error_code'], code, result)
        if posts is not None: self.assertEqual(len(self.payloads), posts)
        if reads is not None: self.assertEqual(self.reads, reads)
        from alden_chat_turns import get_request
        saved = get_request(self.root, self.value['request_id'])
        self.assertNotEqual(saved['state'], 'completed')
        self.assertFalse(saved.get('reply'))

    def test_actual_read_nonce_role_trace_one_commit_and_replay(self):
        result = self.run_input()
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['reply'], self.nonce)
        self.assertEqual((len(self.payloads), self.reads, self.leases), (2,1,1))
        self.assertLessEqual(self.timeouts[1], self.timeouts[0])
        generation = result['generation']
        self.assertEqual(generation['tool_loop']['read_count'], 1)
        self.assertEqual(generation['tool_loop']['hop_count'], 2)
        self.assertEqual(generation['tool_loop']['state'], 'read_completed')
        self.assertNotIn(self.nonce, json.dumps(generation))
        self.assertNotIn('first_output_ms', generation.get('timing', {}))
        replay = self.run_input()
        self.assertTrue(replay['ok'], replay)
        self.assertEqual(replay['reply'], self.nonce)
        self.assertEqual((len(self.payloads), self.reads, self.leases), (2,1,1))

    def test_no_call_final_has_no_read_evidence(self):
        self.behavior = lambda p,n: completion('final', text='자료를 조회하지 않았습니다')
        result = self.run_input()
        self.assertTrue(result['ok'], result)
        self.assertEqual((len(self.payloads),self.reads), (1,0))
        self.assertEqual(result['generation']['tool_loop']['state'], 'no_call')

    def test_denied_project_does_not_broaden(self):
        self.store.register(**self.registration, permission='denied')
        result = self.run_input()
        self.assertTrue(result['ok'], result)
        self.assertNotIn(self.nonce, result['reply'])
        self.assertIn('"state": "empty"', self.payloads[1]['messages'][-1]['content'])

    def test_model_query_arguments_cannot_change_scope(self):
        value = completion()
        value['choices'][0]['message']['tool_calls'][0]['function']['arguments'] = '{"query":"other"}'
        self.behavior = lambda p,n: value
        self.assert_failed(self.run_input(), 'local_llm_tool_protocol_invalid', posts=1, reads=0)

    def test_two_calls_cannot_execute_first(self):
        value = completion();value['choices'][0]['message']['tool_calls'] *= 2
        self.behavior = lambda p,n: value
        self.assert_failed(self.run_input(), 'local_llm_tool_protocol_invalid', posts=1, reads=0)

    def test_second_call_rejected(self):
        self.behavior = lambda p,n: completion(identifier='reply-2') if n==2 else None
        self.assert_failed(self.run_input(), 'local_llm_tool_protocol_invalid', posts=2, reads=1)

    def test_unexpected_sse_rejected_before_read(self):
        self.fake_mime = 'text/event-stream'
        self.assert_failed(self.run_input(), 'local_llm_tool_protocol_invalid', posts=1, reads=0)

    def test_outer_duplicate_json_rejected(self):
        raw = json.dumps(completion()).encode()
        self.behavior = lambda p,n: b'{"id":"duplicate",'+raw[1:]
        self.assert_failed(self.run_input(), 'local_llm_tool_protocol_invalid', posts=1, reads=0)

    def test_response_budget_rejected(self):
        self.behavior = lambda p,n: b' ' * (65536+1)
        self.assert_failed(self.run_input(), 'local_llm_response_too_large', posts=1, reads=0)

    def test_cancel_during_first_response_before_tool(self):
        def cancel(p,n): self.token.cancel();return completion()
        self.behavior = cancel
        self.assert_failed(self.run_input(), 'assistant_input_cancelled', posts=1, reads=0)

    def test_cancel_during_final_response_prevents_commit(self):
        def cancel(p,n):
            if n==2:self.token.cancel();return completion('final',identifier='reply-2')
        self.behavior = cancel
        self.assert_failed(self.run_input(), 'assistant_input_cancelled', posts=2, reads=1)

    def test_input_marker_during_response_is_preserved(self):
        def cancel(p,n):
            self.token.marker.parent.mkdir(mode=0o700,exist_ok=True)
            self.token.marker.write_text('{}');return completion()
        self.behavior = cancel
        self.assert_failed(self.run_input(), 'assistant_input_cancelled', posts=1, reads=0)

    def test_abort_resume_does_not_revive_original_epoch(self):
        def cancel(p,n):
            control=AbortController(self.root);control.abort('test');control.resume_after_human_action();return completion()
        self.behavior = cancel
        self.assert_failed(self.run_input(), 'assistant_input_cancelled', posts=1, reads=0)

    def test_revocation_during_second_response_prevents_commit(self):
        def revoke(p,n):
            if n==2:self.store.register(**self.registration,permission='denied')
        self.behavior = revoke
        self.assert_failed(self.run_input(), 'local_knowledge_source_changed', posts=2, reads=1)

    def test_source_advance_during_second_response_prevents_commit(self):
        def change(p,n):
            if n==2:self.store.ingest(self.target,[{'original_id':'record-1','label':'scopeprobe source','text':'scopeprobe changed'}])
        self.behavior=change
        self.assert_failed(self.run_input(), 'local_knowledge_source_changed', posts=2, reads=1)

    def test_deadline_uses_original_token_without_mutating_epoch(self):
        original = self.token.captured_epoch
        clock = [0.]
        bounded = loop.KnowledgeDeadlineToken(self.token, 1., clock=lambda:clock[0])
        self.assertFalse(bounded.is_cancelled());clock[0]=2.
        self.assertTrue(bounded.is_cancelled())
        with self.assertRaisesRegex(RuntimeError,'local_knowledge_deadline_exceeded'):bounded.raise_if_cancelled()
        self.assertFalse(self.token.is_cancelled());self.assertEqual(self.token.captured_epoch,original)

    def test_total_deadline_during_response_prevents_read(self):
        clock = [10.]
        def expire(p,n): clock[0] = 131.;return completion()
        self.behavior = expire
        with patch.object(loop.time, 'monotonic', side_effect=lambda:clock[0]):
            self.assert_failed(self.run_input(), 'local_knowledge_deadline_exceeded', posts=1, reads=0)
        self.assertFalse(self.token.is_cancelled())

    def test_revocation_after_lookup_prevents_second_model_call(self):
        import alden_knowledge_authority as authority
        original = authority.capture_authority
        def capture(*args):
            receipt = original(*args)
            self.store.register(**self.registration, permission='denied')
            return receipt
        with patch.object(authority, 'capture_authority', side_effect=capture):
            self.assert_failed(self.run_input(), 'local_knowledge_source_changed', posts=1, reads=1)

    def test_cancel_after_lookup_prevents_second_model_call(self):
        import alden_knowledge_authority as authority
        original = authority.capture_authority
        def capture(*args):
            receipt = original(*args);self.token.cancel();return receipt
        with patch.object(authority, 'capture_authority', side_effect=capture):
            self.assert_failed(self.run_input(), 'assistant_input_cancelled', posts=1, reads=1)

    def test_tool_result_budget_prevents_second_model_call(self):
        with patch.object(loop, 'MAX_TOOL_BYTES', 10):
            self.assert_failed(self.run_input(), 'local_knowledge_result_too_large', posts=1, reads=1)

    def test_newer_turn_during_response_supersedes_original(self):
        from alden_chat_turns import reserve_turn
        def supersede(p,n):
            from alden_chat_turns import get_request
            ticket = get_request(self.root, self.value['request_id'])
            reserve_turn(self.root, uuid.uuid4().hex, ticket['conversation_id'], '새 입력')
            return completion()
        self.behavior = supersede
        self.assert_failed(self.run_input(), 'assistant_input_superseded', posts=1, reads=0)

    def test_final_metadata_budget_includes_later_context(self):
        from alden_chat_metrics import MAX_GENERATION_JSON_BYTES
        ticket = {'conversation_id':'a'*32,'turn_id':1,'context_version':1}
        with patch('alden_chat_files.document_evidence', return_value={'explanation':'x'*MAX_GENERATION_JSON_BYTES}):
            with self.assertRaisesRegex(ValueError,'generation_metadata_invalid'):
                chat.generation_evidence({}, {}, ticket, 'completed')

    def test_final_hop_strips_leading_think_wrapper(self):
        self.behavior = lambda p,n: completion('final',identifier='reply-2',
            text='<think>합성 내부 채널</think>\n공개 답변') if n==2 else None
        result = self.run_input()
        self.assertTrue(result['ok'],result);self.assertEqual(result['reply'],'공개 답변')

    def test_think_only_response_is_not_a_visible_answer(self):
        self.behavior = lambda p,n: completion('final',text='<think>합성 내부 채널</think>')
        self.assert_failed(self.run_input(),'local_llm_reply_empty',posts=1,reads=0)

    def test_failed_second_hop_keeps_tool_decision_out_of_final_response(self):
        self.behavior = lambda p,n: b'invalid-json' if n==2 else None
        self.assert_failed(self.run_input(),'local_llm_tool_protocol_invalid',posts=2,reads=1)
        from alden_chat_turns import get_request
        saved = get_request(self.root,self.value['request_id'])
        self.assertEqual(len(saved['generation']['tool_loop']['hops']),1)
        self.assertNotIn('response',saved['generation'])

    def test_failure_telemetry_rejects_impossible_read_sequences(self):
        from alden_chat_metrics import sanitize_generation
        ticket={'conversation_id':'a'*32,'turn_id':1,'context_version':1}
        final={'id':'reply-1','model':MODEL,'kind':'final','usage':{}}
        tool={**final,'kind':'tool'}
        for kinds,reads,extra in [([],1,{}),([final],1,{}),([tool],0,{'read':{'state':'empty','mode':'bm25_only','sources':0}})]:
            with self.subTest(kinds=kinds,reads=reads):
                with self.assertRaisesRegex(ValueError,'generation_metadata_invalid'):
                    sanitize_generation({'transport':'local'},{'tool_loop':{
                        'state':'failed','hops':kinds,'read_count':reads,**extra}},ticket)

    def test_typed_tool_result_has_no_voice_sentence_limit(self):
        result=self.run_input();self.assertTrue(result['ok'],result)
        self.assertNotIn('음성 답변은',self.payloads[1]['messages'][-1]['content'])

    def test_source_revoked_during_second_readiness_check_is_not_sent_to_model(self):
        calls = [0]
        def ready(_token):
            calls[0] += 1
            if calls[0] == 2:self.store.register(**self.registration,permission='denied')
        voice.LocalMlxLlm._require_selected_model_ready.side_effect = ready
        self.assert_failed(self.run_input(),'local_knowledge_source_changed',posts=1,reads=1)

    def test_unclosed_leading_think_is_not_a_visible_answer(self):
        self.behavior=lambda p,n: completion('final',text='<think>합성 미완결 채널')
        self.assert_failed(self.run_input(),'local_llm_reply_empty',posts=1,reads=0)

    def test_fenced_think_example_remains_literal_visible_text(self):
        example='예시:\n```xml\n<think>문자열 예시</think>\n```'
        self.behavior=lambda p,n: completion('final',text=example)
        result=self.run_input();self.assertTrue(result['ok'],result);self.assertEqual(result['reply'],example)


if __name__ == '__main__':
    unittest.main()
