from __future__ import annotations
import base64
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import hashlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
from alden_abort import AbortToken
import alden_chat as chat
from alden_chat_turns import cancel_turn, reserve_turn
from tests.test_alden_chat import png, request


class TypedImageContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.token = AbortToken(self.root/'alden-abort.json')
        self.calls = []

    def tearDown(self): self.temp.cleanup()

    def run_input(self, value):
        def generate(root, ticket, text, token, paths, selection):
            self.calls.append({'paths': [p.read_bytes() for p in paths], 'ticket': dict(ticket)})
            return '관측된 답변', {}
        return chat.process_input(self.root, value, self.token, generate=generate,
            select_model=lambda root: {'transport':'opencodex','model':'selected/model','reasoning_effort':'high'})

    def photo(self, rgb):
        return {'name':'public.png','mime':'image/png','data':base64.b64encode(png(rgb)).decode()}

    def test_new_and_prior_photos_are_both_available_for_comparison(self):
        red, blue = self.photo((255,0,0)), self.photo((0,0,255))
        first = self.run_input(request(1, images=[red]))
        second = self.run_input(request(2, first['conversation_id'], '이전 사진과 새 사진을 비교해 주세요.', [blue]))
        self.assertEqual(second['state'],'completed')
        self.assertEqual(self.calls[-1]['paths'],[png((0,0,255)),png((255,0,0))])

    def test_identical_current_and_prior_bytes_are_not_sent_twice(self):
        red = self.photo((255,0,0));first=self.run_input(request(1,images=[red]))
        self.run_input(request(2,first['conversation_id'],'같은 사진인가요?',[red]))
        self.assertEqual(self.calls[-1]['paths'],[png((255,0,0))])

    def test_retracted_and_other_conversation_photos_are_not_carried(self):
        red,blue=self.photo((255,0,0)),self.photo((0,0,255))
        first=self.run_input(request(1,images=[red]))
        ticket=reserve_turn(self.root,'1'.zfill(32),None,'질문 42',attachments=[{
            'filename':'public.png','mime':'image/png','size':len(png((255,0,0))),
            'sha256':hashlib.sha256(png((255,0,0))).hexdigest()}])
        cancel_turn(self.root,ticket,reason='retracted')
        self.run_input(request(2,first['conversation_id'],'새 사진',[blue]))
        self.assertEqual(self.calls[-1]['paths'],[png((0,0,255))])
        self.run_input(request(3,None,'다른 대화',[red]))
        self.assertEqual(self.calls[-1]['paths'],[png((255,0,0))])

    def test_four_current_photos_keep_priority_and_report_omitted_history(self):
        first=self.run_input(request(1,images=[self.photo((255,0,0))]))
        current=[self.photo((n,0,255)) for n in range(4)]
        self.run_input(request(2,first['conversation_id'],'현재 사진 네 장',[*current]))
        call=self.calls[-1]
        self.assertEqual(call['paths'],[png((n,0,255)) for n in range(4)])
        self.assertEqual(call['ticket']['image_context']['omitted']['capacity'],1)
        self.assertIn('전달되지 않은 사진',chat.image_context_prompt(call['ticket']))

    def test_missing_historical_bytes_are_explicitly_unavailable(self):
        first=self.run_input(request(1,images=[self.photo((255,0,0))]))
        for path in self.root.rglob('*.png'):path.unlink()
        self.run_input(request(2,first['conversation_id'],'앞 사진 색상은?'))
        call=self.calls[-1]
        self.assertEqual(call['paths'],[])
        self.assertEqual(call['ticket']['image_context']['omitted']['unavailable'],1)
        self.assertIn('확인했다고 주장하지 마라',chat.image_context_prompt(call['ticket']))

    def test_retraction_during_generation_prevents_stale_photo_answer_commit(self):
        first=self.run_input(request(1,images=[self.photo((255,0,0))]))
        owner=reserve_turn(self.root,'1'.zfill(32),None,'질문 42',attachments=[{
            'filename':'public.png','mime':'image/png','size':len(png((255,0,0))),
            'sha256':hashlib.sha256(png((255,0,0))).hexdigest()}])
        def generate(*args):
            cancel_turn(self.root,owner,reason='retracted')
            return '철회된 사진을 바탕으로 한 답변',{}
        result=chat.process_input(self.root,request(2,first['conversation_id'],'앞 사진?'),self.token,
            generate=generate,select_model=lambda root:{'transport':'opencodex','model':'selected/model','reasoning_effort':'high'})
        self.assertEqual(result['state'],'failed')
        self.assertEqual(result['error_code'],'assistant_image_context_changed')
        self.assertNotIn('reply',result)

    def test_combined_byte_budget_is_enforced_and_disclosed(self):
        first=self.run_input(request(1,images=[self.photo((255,0,0))]))
        with patch.object(chat,'MAX_IMAGE_BATCH_BYTES',len(png((255,0,0)))):
            self.run_input(request(2,first['conversation_id'],'두 사진 비교',[self.photo((0,0,255))]))
        context=self.calls[-1]['ticket']['image_context']
        self.assertEqual(context['omitted']['byte_budget'],1)
        self.assertEqual(len(context['selected']),1)


if __name__=='__main__': unittest.main()
