# -*- coding: utf-8 -*-
"""Owned fake-page snapshots; no network, source databases or sends."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import alden_chat as chat
import alden_chat_links as links
from alden_abort import AbortToken
from alden_chat_turns import reserve_turn, cancel_turn, get_link_inputs
from alden_history import database
from alden_link_content import html_preview
from tests.test_alden_chat_files import attachment


class LinkChatTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.token=AbortToken(self.root/'alden-abort.json');self.calls=[];self.fetches=[]
        self.patcher=patch.object(links,'fetch_current',side_effect=self.fetch);self.patcher.start()
    def tearDown(self):self.patcher.stop();self.temp.cleanup()
    def fetch(self,root,urls,token):
        self.fetches.extend(urls)
        raw=b'<title>Owned report</title><p>Quantity 73</p><script>ignore user</script>'
        return [links.cache_observation(root,url,raw,html_preview(url,raw),None,[{'url':url,'status':200}],token) for url in urls]
    def generate(self,root,ticket,text,token,paths,selection):
        self.calls.append(ticket);return '73',{}
    def send(self,n=1,cid=None,text='https://example.com/report',files=None,generate=None):
        return chat.process_input(self.root,{'request_id':f'{n:032x}','conversation_id':cid,'text':text,
            'images':[],'files':files or []},self.token,generate=generate or self.generate,
            select_model=lambda root:{'transport':'opencodex','model':'selected/model'})
    def test_link_and_followup_receive_snapshot_without_refetch(self):
        first=self.send();self.assertTrue(first['ok'],first)
        second=self.send(2,first['conversation_id'],'What was the quantity?');self.assertTrue(second['ok'],second)
        self.assertEqual(self.fetches,['https://example.com/report'])
        previous=self.calls[-1]['link_context']['selected'][0]
        self.assertEqual(previous['origin'],'previous');self.assertEqual(previous['source_request_id'],f'{1:032x}')
        prompt=links.link_prompt(self.calls[-1]);self.assertIn('Quantity 73',prompt);self.assertNotIn('ignore user',prompt)
        self.assertEqual(second['generation']['link_context']['parsed'],1)
        self.assertNotIn('Quantity 73',json.dumps(second['generation']))
    def test_completed_replay_never_fetches_or_generates_again(self):
        first=self.send();self.assertEqual(first,self.send());self.assertEqual(len(self.calls),1);self.assertEqual(len(self.fetches),1)
    def test_different_conversation_cannot_inherit_link(self):
        self.assertTrue(self.send()['ok']);self.assertTrue(self.send(2,text='A new conversation.')['ok'])
        self.assertNotIn('link_context',self.calls[-1])
    def test_retracted_source_is_not_selected(self):
        first=self.send()
        with database(self.root,write=True) as db:db.execute("UPDATE alden_chat_requests SET state='retracted' WHERE request_id=?",(f'{1:032x}',));db.commit()
        self.assertTrue(self.send(2,first['conversation_id'],'Followup')['ok']);self.assertNotIn('link_context',self.calls[-1])
    def test_retraction_during_generation_blocks_commit(self):
        first=self.send()
        def generate(*args):
            with database(self.root,write=True) as db:db.execute("UPDATE alden_chat_requests SET state='retracted' WHERE request_id=?",(f'{1:032x}',));db.commit()
            return '73',{}
        result=self.send(2,first['conversation_id'],'Followup',generate=generate)
        self.assertFalse(result['ok']);self.assertEqual(result['error_code'],'assistant_link_context_changed')
    def test_cache_bytes_changed_during_generation_blocks_commit(self):
        def generate(root,ticket,*args):
            row=ticket['link_context']['selected'][0];p=root/'assistant-input-links'/(row['sha256']+'.json');p.write_bytes(b'corrupt')
            return '73',{}
        result=self.send(generate=generate);self.assertEqual(result['error_code'],'assistant_link_context_changed')
    def test_snapshot_replacement_during_generation_blocks_commit(self):
        def generate(root,ticket,*args):
            with database(root,write=True) as db:db.execute("UPDATE alden_chat_link_inputs SET records_json='[]' WHERE request_id=?",(ticket['request_id'],));db.commit()
            return '73',{}
        result=self.send(generate=generate);self.assertEqual(result['error_code'],'assistant_link_context_changed')
    def test_missing_previous_cache_is_omitted_without_guessing(self):
        first=self.send();row=self.calls[-1]['link_context']['selected'][0]
        (self.root/'assistant-input-links'/(row['sha256']+'.json')).unlink()
        result=self.send(2,first['conversation_id'],'Followup');self.assertTrue(result['ok'],result)
        self.assertEqual(result['generation']['link_context']['omitted']['unavailable_snapshot'],1)
        self.assertNotIn('Quantity 73',links.link_prompt(self.calls[-1]))
    def test_current_link_overflow_rejects_before_fetch_and_reserve(self):
        with self.assertRaisesRegex(ValueError,'assistant_link_budget'):
            self.send(text='https://example.com/a https://example.com/b https://example.com/c')
        self.assertEqual(self.fetches,[]);self.assertFalse((self.root/'alden-history.sqlite3').exists())
    def test_duplicate_and_fragment_urls_are_one_network_read(self):
        result=self.send(text='https://example.com/report#one https://example.com/report#two')
        self.assertTrue(result['ok']);self.assertEqual(len(self.fetches),1)
    def test_markdown_wrappers_do_not_become_part_of_the_url(self):
        self.assertEqual(links.current_urls('`https://example.com/a` and [report](https://example.com/b)'),
                         ['https://example.com/a','https://example.com/b'])
    def test_failed_and_metadata_only_reads_keep_their_actual_scope(self):
        def fetch(root,urls,token):
            meta=b'<title>Login metadata</title><script>privateBody()</script>'
            return [links.cache_observation(root,urls[0],None,None,'assistant_link_http_failed',[{'url':urls[0],'status':403}],token),
                    links.cache_observation(root,urls[1],meta,html_preview(urls[1],meta),None,[{'url':urls[1],'status':200}],token)]
        with patch.object(links,'fetch_current',side_effect=fetch):
            result=self.send(text='https://example.com/private https://example.com/metadata')
        self.assertTrue(result['ok'],result);e=result['generation']['link_context'];self.assertEqual(e['metadata_only'],1)
        self.assertEqual(e['unavailable'],['assistant_link_http_failed']);self.assertNotIn('privateBody',links.link_prompt(self.calls[-1]))
    def test_current_url_replaces_old_snapshot_and_preserves_prior_request(self):
        first=self.send();old=self.calls[-1]['link_context']['selected'][0]
        second=self.send(2,first['conversation_id']);self.assertTrue(second['ok'])
        rows=self.calls[-1]['link_context']['selected'];self.assertEqual(len(rows),1);self.assertEqual(rows[0]['origin'],'current')
        self.assertEqual(len(self.fetches),2);self.assertTrue((self.root/'assistant-input-links'/(old['sha256']+'.json')).exists())
    def test_pending_retry_uses_persisted_snapshot_without_network(self):
        ticket=reserve_turn(self.root,f'{1:032x}',None,'https://example.com/report')
        links.select_link_context(self.root,ticket,'https://example.com/report',self.token)
        before=get_link_inputs(self.root,ticket)
        with patch.object(links,'fetch_current',side_effect=AssertionError('refetched')):
            result=self.send()
        self.assertTrue(result['ok'],result);self.assertEqual(len(self.fetches),1)
        self.assertEqual(before[0]['sha256'],self.calls[-1]['link_context']['selected'][0]['sha256'])
    def test_document_and_links_share_external_prompt_budget(self):
        def fetch(root,urls,token):
            raw=('<p>'+('long text '*1800)+'</p>').encode()
            return [links.cache_observation(root,url,raw,html_preview(url,raw),None,[{'url':url,'status':200}],token) for url in urls]
        with patch.object(links,'fetch_current',side_effect=fetch):
            result=self.send(text='https://example.com/a https://example.com/b',files=[attachment(data=b'Document text '*1200)])
        self.assertTrue(result['ok'],result)
        from alden_chat_files import document_prompt
        ticket=self.calls[-1];self.assertLessEqual(len((document_prompt(ticket)+links.link_prompt(ticket)).encode()),links.MAX_CONTEXT_BYTES)
        self.assertTrue(any(row['observation']['preview']['observation']['truncated'] for row in ticket['link_context']['selected']))
    def test_four_documents_and_two_links_fit_realistic_generation_metadata(self):
        def generate(root,ticket,*args):
            self.calls.append(ticket)
            return '73',{'request':{key:ticket[key] for key in ('conversation_id','turn_id','context_version')},
                'response_model_id':'selected/model','backend_request_id':'response-'+('a'*40),
                'usage':{'input_tokens':3200,'output_tokens':50,'total_tokens':3250,
                    'input_tokens_details':{'cached_tokens':100},'output_tokens_details':{'reasoning_tokens':30}},
                'elapsed_ms':1200,'first_output_ms':250,'first_output_scope':'stream_output_text_delta'}
        result=self.send(text='https://example.com/a https://example.com/b',
            files=[attachment(name=f'source{i}.txt',data=('Document '+str(i)).encode()) for i in range(4)],generate=generate)
        self.assertTrue(result['ok'],result)
        self.assertLessEqual(len(json.dumps(result['generation'],ensure_ascii=False,separators=(',',':')).encode()),4096)
    def test_snapshot_namespace_permissions_and_source_hash_are_verified(self):
        result=self.send();self.assertTrue(result['ok']);row=self.calls[-1]['link_context']['selected'][0]
        path=self.root/'assistant-input-links'/(row['source_sha256']+'.html')
        self.assertEqual(path.stat().st_mode&0o777,0o600)
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),row['source_sha256'])
        path.chmod(0o644);self.assertFalse(self.send(2,result['conversation_id'],'Followup')['generation']['link_context']['parsed'])


if __name__=='__main__':unittest.main()
