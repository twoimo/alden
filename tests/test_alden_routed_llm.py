import io
import json
import base64
import hashlib
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from alden_abort import AbortController, AldenCancelled
from alden_routed_llm import OpenCodexLlm
from alden_model_routes import DEFAULT_MODEL

class Stream(io.BytesIO):
    headers={'Content-Type':'text/event-stream'}
    def __init__(self,events):
        super().__init__(b''.join(b'data: '+json.dumps(event).encode()+b'\n\n' for event in events))
    def __exit__(self,*args):return False

class JsonResponse(io.BytesIO):
    headers = {'Content-Type': 'application/json'}
    def __init__(self, payload):
        super().__init__(json.dumps(payload).encode())
    def __exit__(self, *args):
        return False

class RoutedLlmTests(unittest.TestCase):
    def setUp(self):
        t=TemporaryDirectory();self.addCleanup(t.cleanup);self.root=Path(t.name)
        self.token=AbortController(self.root).token()
        self.adapter=OpenCodexLlm(DEFAULT_MODEL,'high',self.root)
        self.catalog={'models':[{'id':DEFAULT_MODEL,'selectable':True,'local':False,'efforts':['high']}]}

    def invoke(self,events,**kwargs):
        requests=[]
        def transport(request,*args,**options):requests.append(request);return Stream(events)
        with patch('alden_routed_llm.catalog',return_value=self.catalog),patch('alden_routed_llm.CancellableLocalResponse',side_effect=transport):
            result=self.adapter.generate_messages('CALLER SYSTEM','LATEST USER',self.token,**kwargs)
        return result,requests

    def completed(self, **changes):
        return {'status': 'completed', 'model': DEFAULT_MODEL, 'id': 'response-1',
                'usage': {'output_tokens': 2}, 'output': [{'type': 'message',
                'role': 'assistant', 'content': [{'type': 'output_text', 'text': '확인'}]}], **changes}

    def invoke_json(self, payload, **kwargs):
        with patch('alden_routed_llm.catalog', return_value=self.catalog), \
                patch('alden_routed_llm.CancellableLocalResponse', return_value=JsonResponse(payload)):
            return self.adapter.generate_messages('CALLER SYSTEM', 'LATEST USER', self.token, **kwargs)

    def test_json_requires_completed_exact_model_and_keeps_usage(self):
        self.assertEqual(self.invoke_json(self.completed()), '확인')
        self.assertEqual(self.adapter.last_metrics['backend_request_id'], 'response-1')
        self.assertEqual(self.adapter.last_metrics['usage'], {'output_tokens': 2})

    def test_json_never_publishes_wrong_model_failed_or_unknown_completion(self):
        cases = [({'model': 'gpt-6-astra'}, 'routed_model_mismatch'),
                 ({'model': None}, 'routed_model_mismatch'),
                 ({'status': 'failed'}, 'routed_llm_generation_failed'),
                 ({'status': 'incomplete'}, 'routed_llm_generation_failed'),
                 ({'status': None}, 'routed_llm_incomplete_response'),
                 ({'error': {'code': 'server_error'}}, 'routed_llm_generation_failed')]
        for changes, code in cases:
            with self.subTest(changes=changes), self.assertRaisesRegex(RuntimeError, '^' + code + '$'):
                self.invoke_json(self.completed(**changes))
            self.assertEqual(self.adapter.last_metrics['request']['state'], 'failed')
            health = json.loads((self.root / 'model-route-health.json').read_text())
            self.assertFalse(health[DEFAULT_MODEL]['success'])

    def test_sse_terminal_payload_must_also_confirm_completion(self):
        for status in ('failed', 'incomplete', 'in_progress', None):
            with self.subTest(status=status), self.assertRaises(RuntimeError):
                self.invoke([{'type': 'response.output_text.delta', 'delta': 'PARTIAL'},
                             {'type': 'response.completed', 'response': self.completed(status=status)}])

    def test_json_size_limit_is_checked_before_parse_and_success_recording(self):
        with self.assertRaisesRegex(RuntimeError, '^routed_llm_response_budget$'):
            self.invoke_json(self.completed(padding='x' * (1024 * 1024)))

    def test_malformed_shapes_report_response_error(self):
        for payload in ([], self.completed(output=[None]), self.completed(output=[{'type': 'message', 'role': 'assistant', 'content': None}])):
            with self.subTest(payload=payload), self.assertRaisesRegex(RuntimeError, '^routed_llm_invalid_response$'):
                self.invoke_json(payload)

    def test_unavailable_image_does_not_turn_a_healthy_route_unavailable(self):
        self.invoke_json(self.completed())
        before = (self.root / 'model-route-health.json').read_bytes()
        with self.assertRaisesRegex(RuntimeError, '^image_input_unavailable$'):
            self.invoke_json(self.completed(), image_paths=[self.root / 'missing.png'])
        self.assertEqual((self.root / 'model-route-health.json').read_bytes(), before)

    def test_image_symlink_and_non_image_bytes_never_open_transport(self):
        target = self.root / 'target.png'
        target.write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII='))
        symlink = self.root / 'linked.png'
        symlink.symlink_to(target)
        invalid = self.root / 'text.png'
        invalid.write_text('These are not image bytes')
        for path in (symlink, invalid):
            with self.subTest(path=path.name), patch('alden_routed_llm.CancellableLocalResponse') as transport:
                with self.assertRaisesRegex(RuntimeError, '^image_input_unavailable$'):
                    self.invoke([], image_paths=[path])
                transport.assert_not_called()

    def test_image_payload_and_provenance_use_the_same_bytes_and_signature(self):
        raw = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=')
        path = self.root / 'wrong-extension.jpg'
        path.write_bytes(raw)
        result, requests = self.invoke([{'type': 'response.completed', 'response': self.completed()}], image_paths=[path])
        self.assertEqual(result, '확인')
        parts = json.loads(requests[0].data)['input'][-1]['content']
        self.assertEqual(parts[0], {'type': 'input_text', 'text': 'LATEST USER'})
        self.assertEqual(parts[1]['image_url'], 'data:image/png;base64,' + base64.b64encode(raw).decode())
        self.assertEqual(self.adapter.last_metrics['images'], [{'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw), 'mime': 'image/png'}])

    def test_image_replacement_during_snapshot_never_opens_transport(self):
        path = self.root / 'photo.png'
        path.write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII='))
        fdopen = os.fdopen
        class ReplacingReader:
            def __init__(reader, fd, mode): reader.handle = fdopen(fd, mode)
            def __enter__(reader): return reader
            def __exit__(reader, *args): reader.handle.close()
            def fileno(reader): return reader.handle.fileno()
            def read(reader, limit):
                raw = reader.handle.read(limit)
                path.rename(self.root / 'old.png')
                path.write_bytes(raw)
                return raw
        with patch('alden_routed_llm.os.fdopen', side_effect=ReplacingReader), \
                patch('alden_routed_llm.CancellableLocalResponse') as transport:
            with self.assertRaisesRegex(RuntimeError, '^image_input_unavailable$'):
                self.invoke([], image_paths=[path])
            transport.assert_not_called()

    def test_image_count_and_combined_byte_limits_precede_transport(self):
        path = self.root / 'photo.png'
        path.write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII='))
        for images in ([path] * 5, [path] * 2):
            with self.subTest(count=len(images)), patch('alden_routed_llm.MAX_IMAGE_BATCH_BYTES', 100), \
                    patch('alden_routed_llm.CancellableLocalResponse') as transport:
                with self.assertRaisesRegex(RuntimeError, '^image_input_budget$'):
                    self.invoke([], image_paths=images)
                transport.assert_not_called()

    def test_cancelled_json_never_publishes_completed_answer(self):
        token = self.token
        class LateCancelled(JsonResponse):
            def read(response, limit):
                raw = super().read(limit)
                token.cancel()
                return raw
        with patch('alden_routed_llm.catalog', return_value=self.catalog), \
                patch('alden_routed_llm.CancellableLocalResponse', return_value=LateCancelled(self.completed())):
            with self.assertRaises(AldenCancelled):
                self.adapter.generate_messages('CALLER SYSTEM', 'LATEST USER', token)
        self.assertEqual(self.adapter.last_metrics['request']['state'], 'cancelled')

    def test_exact_high_route_preserves_roles_and_excludes_reasoning_from_output(self):
        result,requests=self.invoke([{'type':'response.reasoning_text.delta','delta':'PRIVATE REASONING'},
            {'type':'response.output_text.delta','delta':'확인'},
            {'type':'response.completed','response':self.completed()}])
        self.assertEqual(result,'확인');self.assertEqual(len(requests),1)
        data=json.loads(requests[0].data)
        self.assertEqual(data['reasoning'],{'effort':'high'})
        self.assertEqual(data['input'],[{'role':'system','content':'CALLER SYSTEM'},{'role':'user','content':'LATEST USER'}])
        self.assertEqual(requests[0].full_url,'http://127.0.0.1:10101/v1/responses')
        self.assertFalse(data['store'])

    def test_missing_terminal_event_and_wrong_returned_model_never_publish_a_partial_answer(self):
        for events in [[{'type':'response.output_text.delta','delta':'PARTIAL'}],
                       [{'type':'response.output_text.delta','delta':'WRONG'}, {'type':'response.completed','response':{'model':'gpt-6-astra'}}]]:
            with self.assertRaises(RuntimeError):self.invoke(events)

    def test_pre_cancelled_turn_does_not_open_any_model_transport(self):
        self.token.cancel()
        with patch('alden_routed_llm.CancellableLocalResponse') as transport:
            with self.assertRaises(AldenCancelled):self.adapter.generate('text',self.token)
            transport.assert_not_called()

    def test_missing_requested_image_fails_instead_of_sending_a_text_only_request(self):
        with patch('alden_routed_llm.CancellableLocalResponse') as transport:
            with self.assertRaises(RuntimeError):self.invoke([],image_paths=[self.root/'missing.png'])
            transport.assert_not_called()

if __name__=='__main__':unittest.main()
