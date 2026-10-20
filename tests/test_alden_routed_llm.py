import io
import http.client
import json
import base64
import hashlib
import os
import socket
import threading
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
    def test_high_reasoning_has_bounded_room_for_reasoning_and_final_answer(self):
        _,requests=self.invoke([{'type':'response.completed','response':self.completed()}])
        self.assertEqual(json.loads(requests[0].data)['max_output_tokens'],2048)
        self.assertEqual(self.adapter.last_metrics['output_token_budget'],2048)
        self.adapter.reasoning_effort='low';self.catalog['models'][0]['efforts']=['low']
        _,requests=self.invoke([{'type':'response.completed','response':self.completed()}])
        self.assertEqual(json.loads(requests[0].data)['max_output_tokens'],1024)

    def test_token_limit_incompletion_preserves_reason_and_does_not_poison_availability(self):
        payload=self.completed(status='incomplete',incomplete_details={'reason':'max_output_tokens'})
        for event in ([{'type':'response.output_text.delta','delta':'PARTIAL'}, {'type':'response.incomplete','response':payload}],None):
            with self.subTest(event=event),patch('alden_routed_llm.record_result') as health:
                with self.assertRaisesRegex(RuntimeError,'^routed_llm_reply_truncated$'):
                    self.invoke_json(payload) if event is None else self.invoke(event)
                health.assert_not_called()
                self.assertEqual(self.adapter.last_metrics['request']['state'],'failed')
                self.assertEqual(self.adapter.last_metrics['incomplete_reason'],'max_output_tokens')
                self.assertEqual(self.adapter.last_metrics['response_model_id'],DEFAULT_MODEL)

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

    def invoke_raw_stream(self, raw):
        response = Stream([])
        response.write(raw)
        response.seek(0)
        with patch('alden_routed_llm.catalog', return_value=self.catalog), \
                patch('alden_routed_llm.CancellableLocalResponse', return_value=response):
            return self.adapter.generate_messages('CALLER SYSTEM', 'LATEST USER', self.token)

    def test_sse_multiline_crlf_and_bom_preserve_one_json_event(self):
        event = {'type': 'response.completed', 'response': self.completed()}
        lines = json.dumps(event, indent=2).encode().splitlines()
        raw = b'\xef\xbb\xbfdata: ' + lines[0] + b'\r\n: comment\r\n' + b''.join(b'data: ' + line + b'\r\n' for line in lines[1:]) + b'\r\n'
        self.assertEqual(self.invoke_raw_stream(raw), '확인')

    def test_sse_whitespace_field_is_not_an_event_boundary(self):
        event = {'type': 'response.completed', 'response': self.completed()}
        lines = json.dumps(event, indent=2).encode().splitlines()
        raw = b'data: ' + lines[0] + b'\n\t\n' + b''.join(b'data: ' + line + b'\n' for line in lines[1:]) + b'\n'
        self.assertEqual(self.invoke_raw_stream(raw), '확인')

    def test_sse_unterminated_terminal_frame_does_not_publish_completion(self):
        event = {'type': 'response.completed', 'response': self.completed()}
        raw = b'data: ' + json.dumps(event).encode() + b'\n'
        with self.assertRaisesRegex(RuntimeError, '^routed_llm_incomplete_response$'):
            self.invoke_raw_stream(raw)
        self.assertEqual(self.adapter.last_metrics['request']['state'], 'failed')

    def test_sse_pending_fields_keep_the_response_byte_budget(self):
        with patch('alden_routed_llm.MAX_RESPONSE_BYTES', 512):
            with self.assertRaisesRegex(RuntimeError, '^routed_llm_response_budget$'):
                self.invoke_raw_stream(b'data: \n' * 100)

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

    def test_first_confirmed_completion_keeps_answer_and_backend_evidence_immutable(self):
        later = self.completed(id='response-2', usage={'output_tokens': 999}, output=[{
            'type': 'message', 'role': 'assistant',
            'content': [{'type': 'output_text', 'text': '다른 답변'}]}])
        for suffix in (
            {'type': 'response.output_text.delta', 'delta': 'LATE'},
            {'type': 'response.completed', 'response': later},
            {'type': 'response.completed', 'response': self.completed(model='wrong-model')},
            {'type': 'response.failed'},
        ):
            with self.subTest(kind=suffix['type'], model=suffix.get('response', {}).get('model')), \
                    patch('alden_routed_llm.record_result') as health:
                answer, _ = self.invoke([{'type': 'response.completed', 'response': self.completed()}, suffix])
                self.assertEqual(answer, '확인')
                self.assertEqual(self.adapter.last_metrics['backend_request_id'], 'response-1')
                self.assertEqual(self.adapter.last_metrics['usage'], {'output_tokens': 2})
                self.assertEqual(self.adapter.last_metrics['response_model_id'], DEFAULT_MODEL)
                health.assert_called_once_with(self.root, DEFAULT_MODEL, success=True)

    def test_confirmed_completion_does_not_wait_for_eof_or_another_frame(self):
        class OpenStream(Stream):
            def readline(response, limit):
                if response.tell() == len(response.getvalue()):
                    raise TimeoutError('stream stayed open after its terminal event')
                return super().readline(limit)
        with patch('alden_routed_llm.catalog', return_value=self.catalog), \
                patch('alden_routed_llm.CancellableLocalResponse', return_value=OpenStream([
                    {'type': 'response.completed', 'response': self.completed()}])):
            self.assertEqual(self.adapter.generate_messages('SYSTEM', 'USER', self.token), '확인')

    def test_cancellation_while_closing_completed_stream_still_prevents_publication(self):
        token = self.token
        class CancelOnClose(Stream):
            def __exit__(response, *args):
                token.cancel()
                return False
        with patch('alden_routed_llm.catalog', return_value=self.catalog), \
                patch('alden_routed_llm.record_result') as health, \
                patch('alden_routed_llm.CancellableLocalResponse', return_value=CancelOnClose([
                    {'type': 'response.completed', 'response': self.completed()}])):
            with self.assertRaises(AldenCancelled):
                self.adapter.generate_messages('SYSTEM', 'USER', token)
            health.assert_not_called()
            self.assertEqual(self.adapter.last_metrics['request']['state'], 'cancelled')

    def test_owned_http_completion_closes_stream_without_waiting_for_server_eof(self):
        # Redirect only the test connection factory to an owned ephemeral
        # listener. The real router, models and user state are never contacted.
        listener = socket.socket()
        self.addCleanup(listener.close)
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        listener.settimeout(2)
        closed = threading.Event()
        errors = []
        frame = b'data: ' + json.dumps({
            'type': 'response.completed', 'response': self.completed()}).encode() + b'\n\n'
        def serve():
            try:
                with listener.accept()[0] as peer:
                    peer.settimeout(2)
                    with peer.makefile('rb') as request:
                        headers = []
                        while True:
                            line = request.readline(65537)
                            if line == b'\r\n':
                                break
                            if not line or len(line) > 65536:
                                raise ValueError('test_request_headers')
                            headers.append(line)
                        length = next(int(line.split(b':', 1)[1]) for line in headers
                                      if line.lower().startswith(b'content-length:'))
                        request.read(length)
                        peer.sendall(b'HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n'
                                     b'Connection: keep-alive\r\n\r\n' + frame)
                        # Keep the server side open until the consumer closes.
                        if peer.recv(1) == b'':
                            closed.set()
            except Exception as error:
                errors.append(type(error).__name__)
        server = threading.Thread(target=serve, daemon=True)
        server.start()
        connection = http.client.HTTPConnection
        with patch('alden_routed_llm.catalog', return_value=self.catalog), \
                patch('alden_local_http.http.client.HTTPConnection', side_effect=lambda *_args, **kwargs:
                      connection('127.0.0.1', listener.getsockname()[1], **kwargs)):
            self.assertEqual(self.adapter.generate_messages('SYSTEM', 'USER', self.token, timeout=1), '확인')
        self.assertTrue(closed.wait(2), 'the owned consumer left its completed socket open')
        server.join(2)
        self.assertFalse(server.is_alive())
        self.assertEqual(errors, [])

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
