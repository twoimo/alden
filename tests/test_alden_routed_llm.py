import io
import json
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

    def test_exact_high_route_preserves_roles_and_excludes_reasoning_from_output(self):
        result,requests=self.invoke([{'type':'response.reasoning_text.delta','delta':'PRIVATE REASONING'},
            {'type':'response.output_text.delta','delta':'확인'},
            {'type':'response.completed','response':{'model':DEFAULT_MODEL,'id':'response-1','usage':{'output_tokens':2}}}])
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
