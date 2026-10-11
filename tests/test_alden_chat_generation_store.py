from pathlib import Path
import json
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from alden_chat_turns import reserve_turn,complete_turn,cancel_turn,StaleTurn,get_request
from alden_history import database,read

class GenerationStoreTests(unittest.TestCase):
    def test_cancelled_observations_are_durable_without_answer_and_not_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            ticket=reserve_turn(root,'3'*32,None,'취소할 질문')
            metric={'schema_version':1,'terminal_state':'cancelled','timing':{'elapsed_ms':12}}
            cancel_turn(root,ticket,generation=metric)
            self.assertEqual(get_request(root,'3'*32)['generation'],metric)
            cancel_turn(root,ticket,reason='failed',generation={'schema_version':1})
            self.assertEqual(get_request(root,'3'*32)['generation'],metric)
            with database(root) as db:
                self.assertEqual(db.execute("SELECT count(*) FROM voice_messages WHERE role='assistant'").fetchone()[0],0)

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()

    def test_generation_is_atomic_with_answer_and_immutable_on_replay(self):
        ticket=reserve_turn(self.root,'1'*32,None,'질문')
        metric={'schema_version':1,'selection':{'model_id':'selected/model'},'observed':{'elapsed_ms':0}}
        saved=complete_turn(self.root,ticket,'답변',generation=metric)
        self.assertEqual(saved['generation'],metric)
        replay=complete_turn(self.root,ticket,'다른 답변',generation={'schema_version':1})
        self.assertEqual(replay['generation'],metric)
        self.assertEqual(get_request(self.root,'1'*32)['generation'],metric)
        result=read(self.root,Path('/unused'),'voice-history-messages',chat_id=ticket['conversation_id'])
        self.assertNotIn('generation',next(x for x in result['items'] if x['role']=='user'))
        self.assertEqual(next(x for x in result['items'] if x['role']=='assistant')['generation'],metric)

    def test_superseded_turn_cannot_store_generation_or_answer(self):
        old=reserve_turn(self.root,'1'*32,None,'이전 질문')
        reserve_turn(self.root,'2'*32,old['conversation_id'],'새 질문')
        with self.assertRaises(StaleTurn):complete_turn(self.root,old,'오래된 답변',generation={'schema_version':1})
        with database(self.root) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM alden_chat_generations').fetchone()[0],0)
            self.assertEqual(db.execute("SELECT count(*) FROM voice_messages WHERE role='assistant'").fetchone()[0],0)

    def test_invalid_generation_keeps_pending_state_and_has_no_partial_answer(self):
        ticket=reserve_turn(self.root,'1'*32,None,'질문')
        for metadata in [{'elapsed_ms':float('nan')},{'raw':'x'*4096},['bad']]:
            with self.assertRaisesRegex(ValueError,'generation_metadata_invalid'):
                complete_turn(self.root,ticket,'답변',generation=metadata)
        self.assertEqual(get_request(self.root,'1'*32)['state'],'running')
        with database(self.root) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM alden_chat_generations').fetchone()[0],0)

    def test_old_receipt_and_history_remain_readable_without_observation(self):
        ticket=reserve_turn(self.root,'1'*32,None,'오래된 질문')
        saved=complete_turn(self.root,ticket,'답변')
        self.assertNotIn('generation',saved)
        with database(self.root,write=True) as db:db.execute('DROP TABLE alden_chat_generations')
        self.assertEqual(get_request(self.root,'1'*32)['reply'],'답변')
        result=read(self.root,Path('/unused'),'voice-history-messages',chat_id=ticket['conversation_id'])
        self.assertTrue(result['ok'])

if __name__=='__main__':unittest.main()
