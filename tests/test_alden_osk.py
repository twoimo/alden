"""Exercise the bundled, unmodified OSK engine, not a replacement fake writer."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
PRELUDE = r'''
import sys, json
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import alden_osk as a
root = Path(sys.argv[2])
source = {'ok': True, 'indexed_at': 42, 'stale': False, 'nodes': [
 {'id':'chat:1', 'label':'한 공간', 'category':'room', 'description':'대화 공간', 'facts':[], 'evidence':{'kind':'ledger','chat_id':'1'}},
 {'id':'memory:one', 'label':'맥락', 'category':'memory', 'description':'맥락을 이어가는 기억', 'facts':['최신 턴을 보존합니다.'], 'evidence':{'kind':'ledger','source_event_ids':['turn:1'],'chat_id':'1'}}],
 'edges':[{'source':'chat:1','target':'memory:one','relation':'mentions','weight':3,'room_id':'1','evidence':{'kind':'ledger','chat_id':'1'}}]}
'''


class OskIntegrationTests(unittest.TestCase):
    def test_review_retired_generated_hub_is_not_recreated_or_marked_pending(self):
        result=self.execute(r'''
a.synchronize(root,source);c,g,_,w=a._load_engine(root);home=a._home(root)
receipt=w.create_node('과거 분류','과거 자동 입구','올든이 관리하는 로컬 대화 지식입니다.','agent',space=a.SOURCE_SPACE+'/과거 분류')
p=g.Index().by_id[receipt['id']][0]
old={'title':p.stem,'space':str(p.parent.relative_to(home/'vault')),'osk_id':receipt['id'],'written_hash':a.digest(p.read_bytes()),'retired':True}
state=a._read_json(home/'sync.json');state.setdefault('groups',{})['old']=dict(old,retired=False)
state['organization']['hubs']['legacy:old']=old;p.unlink();a._save(home/'sync.json',state)
status=a.synchronize(root,source)
assert status['conflicts']==0 and not p.exists() and a._read_json(home/'sync.json')['layout_pending']==0
print(json.dumps({'retiredPreserved':True}))
''')
        self.assertTrue(result['retiredPreserved'])

    def test_observed_titles_require_same_capture_account_and_preserve_good_cache_on_failure(self):
        result = self.execute('''
import sqlite3
account='1'*64;folder=root/'knowledge/corpus'/account;folder.mkdir(parents=True)
(root/'knowledge/corpus/current.json').write_text(json.dumps({'schema_version':1,'account':account,'snapshot':'published'}))
with sqlite3.connect(folder/'context.sqlite3') as db:
 db.execute('CREATE TABLE corpus_meta(key TEXT,value TEXT)')
 db.executemany('INSERT INTO corpus_meta VALUES (?,?)',[('account',account),('snapshot','published'),('complete','1')])
a._home(root).mkdir(parents=True)
packet={'schema_version':1,'account_fingerprint':account,'observed_at':1000,'rooms':[
 {'chat_id':42,'label_kind':'room_title','room_title':'확인한 방'},
 {'chat_id':84,'label_kind':'default_display_name','display_name':'확인한 표시'},
 {'chat_id':99,'label_kind':'participant_alias','chat_name':'지역 별칭'},
 {'chat_id':149,'label_kind':'room_title','room_title':'중복1'},
 {'chat_id':149,'label_kind':'room_title','room_title':'중복2'}]}
before=a._source_signature(root)
good=a.capture_room_observations(root,Path('/fake'),collect=lambda *args:packet,now=1000)
p=folder/'room-observations.json';data=p.read_bytes()
assert good['state']=='ready' and good['rooms']==3 and a._source_signature(root)!=before
saved=json.loads(data);assert {r['chat_id'] for r in saved['rooms']}=={'42','84','99'}
assert next(r for r in saved['rooms'] if r['chat_id']=='99')['label_kind']=='display_name'
packet['observed_at']=1001;packet['rooms']=[]
preserved=a.capture_room_observations(root,Path('/fake'),collect=lambda *args:packet,now=1001)
assert preserved['state']=='ready' and json.loads(p.read_text())['rooms']==saved['rooms']
data=p.read_bytes()
packet['account_fingerprint']='2'*64
bad=a.capture_room_observations(root,Path('/fake'),collect=lambda *args:packet,now=1001)
assert bad['state']=='unavailable' and p.read_bytes()==data
blocked=a.capture_room_observations(root,Path('/fake'),collect=lambda *args:1/0,now=1060)
assert blocked['state']=='backoff' and p.read_bytes()==data
print(json.dumps({'sameCapture':True,'kept':True}))
''')
        self.assertEqual(result, {'sameCapture': True, 'kept': True})

    def test_read_only_audit_uses_same_graph_and_does_not_bootstrap_or_write(self):
        result = self.execute('''
assert not a.read_graph(root,read_only=True)['ok'] and not (root/'knowledge').exists()
a.synchronize(root,source)
def files():
 return {str(p.relative_to(root)):(a.digest(p.read_bytes()),p.stat().st_mtime_ns) for p in root.rglob('*') if p.is_file()}
before=files();view=a.read_graph(root,read_only=True);after=files()
assert before==after and view['ok'] and any(n['is_hub'] for n in view['nodes'])
assert view==a.read_graph(root)
print(json.dumps({'readOnly':True,'sameGraph':True}))
''')
        self.assertEqual(result, {'readOnly': True, 'sameGraph': True})

    def test_clicked_actor_details_keep_exact_room_account_and_original_roles(self):
        result = self.execute(r'''
import sqlite3
account='1'*64
actor='person:kakao:'+account+':actor:7'
room42='chat:kakao:'+account+':room:42'
room84='chat:kakao:'+account+':room:84'
source['nodes'] += [
 {'id':actor,'label':'동일 이름','category':'대화 상대','description':'여러 방에서 수집된 화자','facts':['다른 방의 캐시 문장'], 'evidence':{'kind':'local_db_snapshot'}},
 {'id':room42,'label':'선택한 방','category':'대화방','facts':[],'evidence':{'kind':'local_db_snapshot'}},
 {'id':room84,'label':'다른 방','category':'대화방','facts':[],'evidence':{'kind':'local_db_snapshot'}}]
a.synchronize(root,source)
p=root/'knowledge/corpus'/account/'context.sqlite3';p.parent.mkdir(parents=True)
with sqlite3.connect(p) as db:
 db.executescript('CREATE TABLE corpus_meta(key TEXT PRIMARY KEY,value TEXT);CREATE TABLE alden_messages(id INTEGER PRIMARY KEY,chat_id TEXT,log_id TEXT,author_id TEXT,user_name TEXT,message TEXT,date TEXT,is_self INTEGER,message_type INTEGER);')
 db.execute('INSERT INTO corpus_meta VALUES (?,?)',('account',account))
 db.executemany('INSERT INTO alden_messages VALUES (?,?,?,?,?,?,?,?,?)',[
  (1,'42','1','7','동일 이름','금요일 3시 회의','2026-10-01 15:00:00',1,1),
  (2,'84','2','7','동일 이름','다른 방 영화 내용','2026-10-02 15:00:00',0,1),
  (3,'42','3','8','동일 이름','동명이인 문장','2026-10-02 16:00:00',0,1)])
(root/'knowledge/corpus/current.json').write_text(json.dumps({'schema_version':1,'account':account}))
payload=a.read_focus(root,actor,chat_id='42')
assert payload['ok'] and len(payload['sources'])==1
assert payload['sources'][0]['room_title']=='선택한 방'
assert payload['sources'][0]['source_role']=='outgoing_unclassified'
assert payload['details']['key_facts']==[] and payload['details']['scope_room_id']=='42'
assert '다른 방' not in json.dumps(payload,ensure_ascii=False) and '동명이인' not in json.dumps(payload,ensure_ascii=False)
foreign=a.read_focus(root,actor,chat_id='kakao:'+('2'*64)+':room:42')
assert not foreign['ok'] and foreign['reason']=='focus_room_scope_invalid'
mismatch=a.read_focus(root,room42,chat_id='84');assert not mismatch['ok']
missing=a.read_focus(root,actor,chat_id='99');assert missing['sources']==[] and '찾지 못했습니다' in missing['details']['summary']
contract,graph,_,write=a._load_engine(root);item=json.loads((a._home(root)/'sync.json').read_text())['managed'][actor]
path=graph.Index().by_id[item['osk_id']][0]
write.update_node(item['osk_id'],body='사용자가 직접 정정한 인물 기록',summary='사용자의 현재 정정',expect_hash=a.digest(path.read_bytes()))
corrected=a.read_focus(root,actor,chat_id='42')
assert corrected['facts'][0]=='사용자의 현재 정정' and corrected['details']['basis']=='note'
assert len(corrected['sources'])==1 and corrected['details']['scope_room_id']=='42'
print(json.dumps({'scoped':True,'classified':False}))
''')
        self.assertTrue(result['scoped'])

    def execute(self, code):
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run([sys.executable, "-E", "-B", "-s", "-c", PRELUDE + code, str(SCRIPTS), str(Path(tmp).resolve())], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)

    def test_real_create_readback_noop_and_incremental_update(self):
        result = self.execute('''
first=a.synchronize(root,source); before=a.read_graph(root)
source['nodes'][1]['updated_at']=12345
second=a.synchronize(root,source)
checkpoint=json.loads((a._home(root)/'sync.json').read_text())
identity=checkpoint['managed']['memory:one']['osk_id']
source['nodes'][1]['description']='更新된 한국어 기억'
third=a.synchronize(root,source); after=a.read_graph(root)
assert sum(n['category']!='collection' for n in before['nodes'])==2 and not [e for e in before['edges'] if e.get('purpose') in ('semantic','reference')]
assert third['changed']==1
assert json.loads((a._home(root)/'sync.json').read_text())['managed']['memory:one']['osk_id']==identity
assert a.read_focus(root,'memory:one')['facts'][0]=='更新된 한국어 기억'
assert list((a._home(root)/'history').rglob('*.md'))
print(json.dumps({'first':first['changed'],'second':second['changed'],'updated':third['changed']}))
''')
        self.assertEqual(result, {"first": 2, "second": 0, "updated": 1})

    def test_human_edits_are_preserved_and_reported(self):
        result = self.execute('''
a.synchronize(root,source)
state=json.loads((a._home(root)/'sync.json').read_text());item=state['managed']['memory:one']
note=a._home(root)/'vault'/item['space']/f"{item['title']}.md"
data=note.read_text()+'\\n사람이 추가한 기억입니다.\\n';note.write_text(data)
source['nodes'][1]['description']='새 자동 요약'
status=a.synchronize(root,source)
assert note.read_text()==data
print(json.dumps({'conflicts':status['conflicts'],'kept':a.read_focus(root,'memory:one')['facts'][1].endswith('사람이 추가한 기억입니다.\\n')}))
''')
        self.assertEqual(result, {"conflicts": 1, "kept": True})

    def test_retraction_hides_but_preserves_note_and_stale_never_retracts(self):
        result = self.execute('''
a.synchronize(root,source)
source['nodes']=source['nodes'][:1];source['edges']=[];source['stale']=True
a.synchronize(root,source);assert sum(n['category']!='collection' for n in a.read_graph(root)['nodes'])==2
source['stale']=False;a.synchronize(root,source)
assert sum(n['category']!='collection' for n in a.read_graph(root)['nodes'])==1
assert len(list((a._home(root)/'vault').rglob('*.md')))>3
print(json.dumps({'retracted':True}))
''')
        self.assertTrue(result["retracted"])

    def test_interrupted_create_recovers_by_exact_readback(self):
        result = self.execute('''
a.synchronize(root,source)
p=a._home(root)/'sync.json';state=json.loads(p.read_text());identity=state['managed'].pop('memory:one')['osk_id'];a._save(p,state)
status=a.synchronize(root,source)
assert json.loads(p.read_text())['managed']['memory:one']['osk_id']==identity
assert sum(n['category']!='collection' for n in a.read_graph(root)['nodes'])==2
print(json.dumps({'recovered':True}))
''')
        self.assertTrue(result["recovered"])

    def test_secret_filter_and_no_global_hooks(self):
        result = self.execute('''
source['nodes'][1]['facts']=['Bearer sk-'+('a'*48)]
a.synchronize(root,source)
raw=(a._home(root)/'sync.json').read_text()+'\\n'.join(p.read_text() for p in (a._home(root)/'vault').rglob('*.md'))
assert 'sk-'+('a'*48) not in raw
assert not (a._home(root)/'vault/.git').exists()
assert not (a._home(root)/'vault/00_Scope/Workbench/_ledger/approvals.jsonl').exists()
print(json.dumps({'filtered':True}))
''')
        self.assertTrue(result["filtered"])

    def test_symlink_target_rejected(self):
        result = self.execute('''
(root/'knowledge').mkdir();(root/'elsewhere').mkdir();(root/'knowledge/osk').symlink_to(root/'elsewhere',target_is_directory=True)
try:a.synchronize(root,source)
except RuntimeError as e:assert str(e)=='osk_symlink_path'
else:raise AssertionError('followed symlink')
assert not list((root/'elsewhere').iterdir())
print(json.dumps({'rejected':True}))
''')
        self.assertTrue(result["rejected"])

    def test_emergency_latch_blocks_writes(self):
        result = self.execute('''
from alden_abort import AbortController
AbortController(root / 'alden-abort.json').abort('test')
status=a.synchronize(root,source)
assert status['state']=='paused' and not (a._home(root)/'sync.json').exists()
print(json.dumps({'paused':True}))
''')
        self.assertTrue(result["paused"])

    def test_manual_osk_note_enters_graph_with_original_id(self):
        result = self.execute('''
a.synchronize(root,source)
contract,graph,secrets,write=a._load_engine(root)
receipt=write.create_node('직접 남긴 기억','사람이 정리한 추가 지식','자세한 메모입니다.','agent',space='00_Scope/Alden')
view=a.read_graph(root)
assert sum(n['category']!='collection' for n in view['nodes'])==3
assert a.read_focus(root,'osk:'+receipt['id'])['facts'][0]=='사람이 정리한 추가 지식'
print(json.dumps({'manual':True}))
''')
        self.assertTrue(result["manual"])

    def test_source_contexts_mix_entity_types_without_claiming_speakers_as_user_facets(self):
        result=self.execute('''
source['nodes'] += [{'id':'person:a','label':'같은 이름','category':'대화 상대','facts':[],'evidence':{'kind':'ledger','chat_id':'1'}},{'id':'person:b','label':'같은 이름','category':'화자','facts':[],'evidence':{'kind':'ledger','chat_id':'2'}}]
a.synchronize(root,source);state=json.loads((a._home(root)/'sync.json').read_text())
contract,graph,secrets,write=a._load_engine(root)
for key in ('person:a','person:b'):
 item=state['managed'][key];assert item['space'].startswith(a.SOURCE_SPACE)
 note=contract.parse(a._home(root)/'vault'/item['space']/(item['title']+'.md'));assert note.id==item['osk_id'];assert not contract.validate(note)
assert state['managed']['person:a']['osk_id']!=state['managed']['person:b']['osk_id']
assert state['managed']['person:a']['space']==state['managed']['chat:1']['space']
assert state['managed']['person:b']['space']==a.SOURCE_SPACE
view=a.read_graph(root);assert not {'대화방','인물','주제'} & {n['label'] for n in view['nodes'] if n['category']=='collection'}
assert not any(e['relation']=='contains' for e in view['edges'])
assert any(e.get('purpose')=='navigation' for e in view['edges'])
print(json.dumps({'sourceSpaces':True,'distinct':True}))
''')
        self.assertEqual(result,{'sourceSpaces':True,'distinct':True})

    def test_multi_room_memories_remain_shared_and_real_sdk_placement_links_are_read_back(self):
        result = self.execute('''
source['nodes'] += [{'id':'chat:2','label':'다른 공간','category':'room','facts':[], 'evidence':{'kind':'ledger','chat_id':'2'}}]
source['nodes'][1]['evidence']['room_ids']=['1','2']
a.synchronize(root,source)
state=json.loads((a._home(root)/'sync.json').read_text())
assert state['managed']['memory:one']['space']==a.SOURCE_SPACE
assert state['managed']['chat:1']['space']==state['managed']['chat:2']['space']==a.SOURCE_SPACE
view=a.read_graph(root)
assert not any(e['relation']=='mentions' for e in view['edges'])
contract,graph,_,_=a._load_engine(root);idx=graph.Index()
byid={n['id']:n for n in view['nodes']}
for e in view['edges']:
 if e.get('purpose')!='navigation':continue
 src=byid[e['source']];target=byid[e['target']]
 path,_=idx.by_id[src['osk_id']];note=contract.parse(path)
 target_path,_=idx.by_id[target['osk_id']]
 assert target_path.stem in note.wikilinks()
 assert src['space']==target['space'] or target['space'].startswith(src['space']+'/')
assert state['layout_pending']==0
print(json.dumps({'shared':True,'realLinks':True}))
''')
        self.assertEqual(result, {'shared': True, 'realLinks': True})

    def test_placement_only_tick_preserves_source_and_retracted_note_reachability(self):
        result = self.execute('''
a.synchronize(root,source)
before=json.loads((a._home(root)/'sync.json').read_text())
source['nodes']=source['nodes'][:1];source['edges']=[]
a.synchronize(root,source)
saved=json.loads((a._home(root)/'sync.json').read_text())
assert not saved['managed']['memory:one']['active']
result=a.reorganize(root);after=json.loads((a._home(root)/'sync.json').read_text())
assert not result['layout_pending'] and not result['conflicts']
assert all(after['managed'][k]['source']==v['source'] for k,v in saved['managed'].items())
contract,graph,_,_=a._load_engine(root);idx=graph.Index()
old=after['managed']['memory:one'];path=a._home(root)/'vault'/old['space']/(old['title']+'.md')
assert path.is_file() and contract.parse(path).id==before['managed']['memory:one']['osk_id']
hub=next(h for h in after['organization']['hubs'].values() if h['space']==old['space'])
assert old['title'] in contract.parse(a._home(root)/'vault'/hub['space']/(hub['title']+'.md')).wikilinks()
assert 'memory:one' not in {n['id'] for n in a.read_graph(root)['nodes']}
print(json.dumps({'retractedReachable':True,'sourcesUnchanged':True}))
''')
        self.assertEqual(result, {'retractedReachable': True, 'sourcesUnchanged': True})

    def test_an_unrendered_source_room_still_prevents_forced_single_room_membership(self):
        result = self.execute('''
source['nodes'][1]['evidence']['room_ids']=['1','unrendered-room']
a.synchronize(root,source)
state=json.loads((a._home(root)/'sync.json').read_text())
assert state['managed']['memory:one']['space']==a.SOURCE_SPACE
assert state['managed']['chat:1']['space']==a.SOURCE_SPACE
print(json.dumps({'unrenderedEvidencePreserved':True}))
''')
        self.assertTrue(result['unrenderedEvidencePreserved'])

    def test_edited_hub_is_preserved_and_organization_is_not_claimed_complete(self):
        result = self.execute('''
a.synchronize(root,source)
state=json.loads((a._home(root)/'sync.json').read_text());hub=state['organization']['hubs']['legacy:kakao']
p=a._home(root)/'vault'/hub['space']/(hub['title']+'.md');text=p.read_text()+'\\n사용자가 정정한 입구.\\n';p.write_text(text)
result=a.reorganize(root)
assert p.read_text()==text and result['conflicts']==1 and result['layout_pending']>=1
print(json.dumps({'kept':True,'pending':True}))
''')
        self.assertEqual(result, {'kept': True, 'pending': True})

    def test_interrupted_sdk_move_recovers_only_the_planned_identical_destination(self):
        result = self.execute('''

a.synchronize(root,source);contract,graph,_,write=a._load_engine(root)
p=a._home(root)/'sync.json';state=json.loads(p.read_text());item=state['managed']['memory:one']
item['planned_move']={'osk_id':item['osk_id'],'title':item['title'],'from_space':item['space'],'dest_space':'00_Scope/Alden','written_hash':item['written_hash']}
a._save(p,state);write.move_node(item['osk_id'],'00_Scope/Alden')
path=graph.Index().by_id[item['osk_id']][0];before=path.read_bytes()
status=a.reorganize(root);after=json.loads(p.read_text())['managed']['memory:one']
assert status['moved']==0 and status['conflicts']==0
assert after['space']=='00_Scope/Alden' and not after.get('planned_move') and path.read_bytes()==before
source['nodes'][1]['evidence']['room_ids']=['new-room'];a.synchronize(root,source)
assert graph.Index().by_id[item['osk_id']][0]==path
print(json.dumps({'recovered':True,'sameBytes':True}))
''')
        self.assertEqual(result, {'recovered': True, 'sameBytes': True})

    def test_human_move_and_rename_are_visible_by_id_and_never_automatically_adopted(self):
        result = self.execute('''
a.synchronize(root,source);contract,graph,_,write=a._load_engine(root)
p=a._home(root)/'sync.json';item=json.loads(p.read_text())['managed']['memory:one'];identity=item['osk_id']
write.move_node(identity,'00_Scope/Alden')
old=graph.Index().by_id[identity][0];renamed=old.with_name('사람이 정정한 제목.md');old.rename(renamed)
data=renamed.read_bytes()
view=a.read_graph(root,read_only=True);node=next(n for n in view['nodes'] if n['id']=='memory:one')
assert node['label']=='사람이 정정한 제목' and node['space']=='00_Scope/Alden' and node['osk_id']==identity
focus=a.read_focus(root,'memory:one');assert focus['ok'] and focus['details']['title']=='사람이 정정한 제목'
source['nodes'][1]['description']='자동으로 덮으면 안 되는 새 요약'
for tick in (lambda:a.reorganize(root),lambda:a.synchronize(root,source),lambda:a.reorganize(root)):
 assert tick()['conflicts']>=1 and renamed.read_bytes()==data
assert graph.Index().by_id[identity][0]==renamed
assert not (a._home(root)/'vault'/item['space']/(item['title']+'.md')).exists()
print(json.dumps({'idVisible':True,'humanPlacementKept':True}))
''')
        self.assertEqual(result, {'idVisible': True, 'humanPlacementKept': True})

    def test_edited_bytes_at_a_planned_destination_are_not_recovered_as_automatic(self):
        result = self.execute('''
a.synchronize(root,source);source['nodes'][1]['evidence']['room_ids']=['1','unresolved']
contract,graph,_,write=a._load_engine(root);real_move=write.move_node
def interrupt(name,dest):
 result=real_move(name,dest);raise RuntimeError('simulated_after_sdk_move')
write.move_node=interrupt
try:a.synchronize(root,source)
except RuntimeError:pass
finally:write.move_node=real_move
item=json.loads((a._home(root)/'sync.json').read_text())['managed']['memory:one'];path=graph.Index().by_id[item['osk_id']][0]
write.update_node(item['osk_id'],body='사람이 목적지에서 정정한 본문',summary='사람의 정정',expect_hash=a.digest(path.read_bytes()))
data=path.read_bytes()
for tick in (lambda:a.reorganize(root),lambda:a.synchronize(root,source)):
 assert tick()['conflicts']>=1 and path.read_bytes()==data
assert a.read_focus(root,'memory:one')['facts'][0]=='사람의 정정'
print(json.dumps({'held':True,'editedBytesKept':True}))
''')
        self.assertEqual(result, {'held': True, 'editedBytesKept': True})

    def test_unchanged_checks_hub_bytes_and_preserves_the_human_correction(self):
        result = self.execute('''
from unittest.mock import patch
a._source_signature=lambda _: 'fixture-source-signature'
with patch('auto_reply_knowledge_graph.collect_knowledge_graph',return_value=source) as collect:
 a.synchronize(root);assert a.synchronize(root)['state']=='unchanged'
 contract,graph,_,write=a._load_engine(root);p=a._home(root)/'sync.json';state=json.loads(p.read_text())
 hub=state['organization']['hubs']['legacy:kakao'];path=graph.Index().by_id[hub['osk_id']][0]
 write.update_node(hub['osk_id'],body='사용자가 정정한 입구',expect_hash=a.digest(path.read_bytes()))
 data=path.read_bytes();status=a.synchronize(root)
 assert status.get('state')!='unchanged' and status['conflicts']>=1 and path.read_bytes()==data
 assert json.loads(p.read_text())['layout_pending']>=1
print(json.dumps({'hubEditSeen':True}))
''')
        self.assertEqual(result, {'hubEditSeen': True})

    def test_new_human_placement_invalidates_an_otherwise_unchanged_vault(self):
        result = self.execute('''
from unittest.mock import patch
a._source_signature=lambda _: 'fixture-source-signature'
with patch('auto_reply_knowledge_graph.collect_knowledge_graph',return_value=source) as collect:
 a.synchronize(root);assert a.synchronize(root)['state']=='unchanged'
 contract,graph,_,write=a._load_engine(root);state=json.loads((a._home(root)/'sync.json').read_text())
 hub=state['organization']['hubs']['root'];hubpath=graph.Index().by_id[hub['osk_id']][0]
 receipt=write.create_node('추가된 직접 기억','추가된 직접 기억','사용자가 남긴 일반 기억','agent',space=hub['space'])
 before=collect.call_count;status=a.synchronize(root)
 assert status.get('state')!='unchanged' and status['conflicts']==0 and collect.call_count==before+1
 assert '추가된 직접 기억' in contract.parse(hubpath).wikilinks()
 assert any(n['id']=='osk:'+receipt['id'] for n in a.read_graph(root)['nodes'])
 assert a.synchronize(root)['state']=='unchanged'
print(json.dumps({'placementSeen':True}))
''')
        self.assertTrue(result['placementSeen'])

    def test_expired_and_retracted_auto_links_do_not_revive_but_manual_links_survive(self):
        result = self.execute('''
from unittest.mock import patch
source['edges'][0]['valid_to']='2099-01-01T00:00:00Z'
a.synchronize(root,source);contract,graph,_,write=a._load_engine(root)
state=json.loads((a._home(root)/'sync.json').read_text());room=state['managed']['chat:1'];topic=state['managed']['memory:one']
path=graph.Index().by_id[topic['osk_id']][0];body=contract.parse(path).body
with patch.object(a.time,'time',return_value=4102444801):
 view=a.read_graph(root);assert not [e for e in view['edges'] if e.get('purpose') in ('semantic','reference')]
 assert '## 대화에서 발견한 관계' not in a.read_focus(root,'memory:one')['facts'][1]
 assert '## 대화에서 발견한 관계' not in a.read_focus(root,'osk:'+topic['osk_id'])['facts'][1]
 # The same target in a separately authored Link is still valid after expiry.
write.update_node(topic['osk_id'],body=body+'\\n\\n## 사람이 덧붙인 관계\\n[['+room['title']+']]',expect_hash=a.digest(path.read_bytes()))
data=path.read_bytes()
with patch.object(a.time,'time',return_value=4102444801):
 edges=a.read_graph(root)['edges'];manual=[e for e in edges if e.get('purpose')=='reference']
 assert len(manual)==1 and manual[0]['source']=='memory:one' and manual[0]['target']=='chat:1'
 assert not any(e.get('purpose')=='semantic' for e in edges)
source['edges'][0]['evidence']['retracted']=True
a.synchronize(root,source);assert path.read_bytes()==data
view=a.read_graph(root);assert len([e for e in view['edges'] if e.get('purpose')=='reference'])==1
assert not any(e.get('purpose')=='semantic' for e in view['edges'])
print(json.dumps({'noRevival':True,'manualLinkKept':True}))
''')
        self.assertEqual(result, {'noRevival': True, 'manualLinkKept': True})

    def test_context_hub_uses_current_source_name_without_renaming_internal_files(self):
        result = self.execute('''
source['nodes'][0]['label']='이름 없는 방'
a.synchronize(root,source);state=json.loads((a._home(root)/'sync.json').read_text())
hub=state['organization']['hubs']['legacy:kakao']
source['nodes'][0]['label']='확인된 현재 방 이름';a.synchronize(root,source)
view=a.read_graph(root);node=next(n for n in view['nodes'] if n['id']=='osk:'+hub['osk_id'])
assert node['label']=='카카오톡' and not any(h.get('room_ref') for h in state['organization']['hubs'].values())
contract,graph,_,_=a._load_engine(root);assert graph.Index().by_id[hub['osk_id']][0].stem==hub['title']
print(json.dumps({'currentDisplayName':True,'stableHubId':True}))
''')
        self.assertEqual(result, {'currentDisplayName': True, 'stableHubId': True})

    def test_identical_human_move_to_the_expected_destination_requires_adapter_intent(self):
        result = self.execute('''
a.synchronize(root,source);contract,graph,_,write=a._load_engine(root)
item=json.loads((a._home(root)/'sync.json').read_text())['managed']['memory:one']
write.move_node(item['osk_id'],'00_Scope/Alden')
path=graph.Index().by_id[item['osk_id']][0];data=path.read_bytes()
source['nodes'][1]['evidence']['room_ids']=['1','unresolved']
status=a.synchronize(root,source);state=json.loads((a._home(root)/'sync.json').read_text())
assert status['conflicts']>=1 and state['managed']['memory:one']['human_corrected']
assert path.read_bytes()==data and not state['managed']['memory:one'].get('planned_move')
assert a.read_focus(root,'memory:one')['details']['space']=='00_Scope/Alden'
print(json.dumps({'humanIntentKept':True}))
''')
        self.assertTrue(result['humanIntentKept'])

    def test_human_hub_rename_is_resolved_by_id_and_not_recreated_or_repaired(self):
        result = self.execute('''
a.synchronize(root,source);contract,graph,_,write=a._load_engine(root)
state=json.loads((a._home(root)/'sync.json').read_text());hub=state['organization']['hubs']['legacy:kakao']
old=graph.Index().by_id[hub['osk_id']][0];directory=old.parent.with_name('사람이 정정한 맥락')
old.parent.rename(directory);renamed=(directory/old.name).rename(directory/(directory.name+'.md'));data=renamed.read_bytes()
node=next(n for n in a.read_graph(root)['nodes'] if n['id']=='osk:'+hub['osk_id']);assert node['label']==directory.name
assert a.read_focus(root,'osk:'+hub['osk_id'])['details']['title']==directory.name
for tick in (lambda:a.reorganize(root),lambda:a.synchronize(root,source)):
 assert tick()['conflicts']>=1 and renamed.read_bytes()==data
assert graph.Index().by_id[hub['osk_id']][0]==renamed and not old.exists()
print(json.dumps({'hubIdVisible':True,'humanHubKept':True}))
''')
        self.assertEqual(result, {'hubIdVisible': True, 'humanHubKept': True})

    def test_legacy_automatic_links_expire_without_removing_separately_authored_links(self):
        result = self.execute('''
from unittest.mock import patch
source['edges'][0]['valid_to']='2099-01-01T00:00:00Z'
a.synchronize(root,source);contract,graph,secrets,write=a._load_engine(root);p=a._home(root)/'sync.json'
state=json.loads(p.read_text());state['edges']=source['edges'];titles={k:v['title'] for k,v in state['managed'].items()}
for key,item in state['managed'].items():
 body=a._body(item['source'],[source['edges'][0]],titles,secrets,legacy=True)
 path=graph.Index().by_id[item['osk_id']][0]
 write.update_node(item['osk_id'],body=body,expect_hash=a.digest(path.read_bytes()))
 item['written_hash']=a.digest(path.read_bytes());item['revision']=a._revision(item['source'],body)
 item.pop('automatic_relation_block')
a._save(p,state)
with patch.object(a.time,'time',return_value=4102444801):
 assert not [e for e in a.read_graph(root)['edges'] if e.get('purpose') in ('semantic','reference')]
topic=state['managed']['memory:one'];path=graph.Index().by_id[topic['osk_id']][0]
body=contract.parse(path).body+'\\n\\n## 직접 작성한 관계\\n[['+titles['chat:1']+']]'
write.update_node(topic['osk_id'],body=body,expect_hash=a.digest(path.read_bytes()));data=path.read_bytes()
source['edges'][0]['evidence']['retracted']=True
a.synchronize(root,source);assert '## 직접 작성한 관계' in contract.parse(path).body and 'mentions:' not in contract.parse(path).body
edges=a.read_graph(root)['edges'];assert len([e for e in edges if e.get('purpose')=='reference'])==1
assert not any(e.get('purpose')=='semantic' for e in edges)
assert '[['+titles['chat:1']+']]' in a.read_focus(root,'memory:one')['facts'][1]
print(json.dumps({'legacyNoRevival':True,'manualLinkKept':True}))
''')
        self.assertEqual(result, {'legacyNoRevival': True, 'manualLinkKept': True})

    def test_clock_expiry_invalidates_unchanged_and_removes_only_automatic_links(self):
        result = self.execute('''
from unittest.mock import patch
a._source_signature=lambda _: 'fixture-source-signature'
source['edges'][0]['valid_to']='2099-01-01T00:00:00Z'
with patch('auto_reply_knowledge_graph.collect_knowledge_graph',return_value=source):
 a.synchronize(root);assert a.synchronize(root)['state']=='unchanged'
 with patch.object(a.time,'time',return_value=4102444801):
  status=a.synchronize(root);assert status['state']=='unchanged' and status['changed']==0
  contract,graph,_,_=a._load_engine(root);state=json.loads((a._home(root)/'sync.json').read_text())
  assert state['edges']==[]
  assert all(not contract.parse(graph.Index().by_id[item['osk_id']][0]).wikilinks() for item in state['managed'].values())
  assert a.synchronize(root)['state']=='unchanged'
print(json.dumps({'clockExpiryApplied':True}))
''')
        self.assertTrue(result['clockExpiryApplied'])

    def test_manual_addition_inside_an_automatic_relation_section_does_not_revive_old_rows(self):
        result = self.execute('''

a.synchronize(root,source);contract,graph,secrets,write=a._load_engine(root)
p=a._home(root)/'sync.json';state=json.loads(p.read_text());state['edges']=source['edges']
titles={k:v['title'] for k,v in state['managed'].items()};topic=state['managed']['memory:one'];room=state['managed']['chat:1']
path=graph.Index().by_id[topic['osk_id']][0]
block=a._relation_block(a._body(topic['source'],source['edges'],titles,secrets,legacy=True),legacy=True)
body=contract.parse(path).body+block+'\\n- 사람이 작성한 링크: [['+room['osk_id']+']]'
write.update_node(topic['osk_id'],body=body,expect_hash=a.digest(path.read_bytes()))
topic['automatic_relation_block']=block;topic['written_hash']=a.digest(path.read_bytes());a._save(p,state)
a.synchronize(root,source);current=contract.parse(path).body
assert 'mentions:' not in current and '[['+room['osk_id']+']]' in current
manual=[e for e in a.read_graph(root)['edges'] if e.get('purpose')=='reference']
assert len(manual)==1 and manual[0]['source']=='memory:one' and manual[0]['target']=='chat:1'
print(json.dumps({'manualInsertionKept':True,'noAutoRevival':True}))
''')
        self.assertEqual(result, {'manualInsertionKept': True, 'noAutoRevival': True})

    def test_duplicate_stable_ids_are_held_without_selecting_or_overwriting_a_copy(self):
        result = self.execute('''
a.synchronize(root,source);contract,graph,_,_=a._load_engine(root)
item=json.loads((a._home(root)/'sync.json').read_text())['managed']['memory:one'];path=graph.Index().by_id[item['osk_id']][0]
copy=path.with_name('사용자가 남긴 동일 ID 사본.md');data=path.read_bytes();copy.write_bytes(data)
assert not a.read_focus(root,'memory:one')['ok']
assert 'memory:one' not in {n['id'] for n in a.read_graph(root)['nodes']}
status=a.reorganize(root);assert status['conflicts']>=1
assert copy.read_bytes()==data and path.read_bytes()==data
print(json.dumps({'ambiguousIdHeld':True,'bothCopiesKept':True}))
''')
        self.assertEqual(result, {'ambiguousIdHeld': True, 'bothCopiesKept': True})

    def test_read_only_validity_boundaries_share_one_clock_and_do_not_save(self):
        result = self.execute('''
from unittest.mock import patch
assert a.read_graph(root,read_only=True)['next_transition_at']==0 and not (root/'knowledge').exists()
source['edges'][0].update(valid_from='2099-01-01T09:00:00.250+09:00',valid_to='2099-01-01T00:00:01.750Z')
a.synchronize(root,source)
def files():
 return {str(p.relative_to(root)):(a.digest(p.read_bytes()),p.stat().st_mtime_ns) for p in root.rglob('*') if p.is_file()}
before=files();start=4070908800.25;end=4070908801.75
for now,active,boundary in [(start-.001,False,start),(start,True,end),(start+.001,True,end),
                            (end-.001,True,end),(end,False,0),(end+.001,False,0)]:
 with patch.object(a.time,'time',return_value=now):
  view=a.read_graph(root,read_only=True)
  assert not [e for e in view['edges'] if e.get('purpose')=='semantic']
  assert view['next_transition_at']==0
  assert not [e for e in view['edges'] if e.get('purpose')=='reference']
 assert files()==before
print(json.dumps({'startInclusive':True,'endExclusive':True,'readOnly':True,'epochSeconds':True}))
''')
        self.assertEqual(result, {'startInclusive': True, 'endExclusive': True, 'readOnly': True, 'epochSeconds': True})

    def test_next_transition_ignores_invalid_past_retracted_and_empty_intervals(self):
        result = self.execute('''
from unittest.mock import patch
template=source['edges'][0]
def edge(relation,begin='',end='',retracted=False,purpose='semantic'):
 return {**template,'relation':relation,'valid_from':begin,'valid_to':end,
         'purpose':purpose,'evidence':{**template['evidence'],'retracted':retracted}}
source['edges']=[
 edge('invalid','not-a-date','2099-99-99T00:00:00Z'),
 edge('past','2000-01-01T00:00:00Z','2001-01-01T00:00:00Z'),
 edge('withdrawn','2099-01-01T00:00:00.100Z','2099-01-01T00:00:00.200Z',True),
 edge('reversed','2099-01-01T00:00:00.200Z','2099-01-01T00:00:00.100Z'),
 edge('empty','2099-01-01T00:00:00.100Z','2099-01-01T00:00:00.100Z'),
 edge('ends-next','invalid','2099-01-01T00:00:00.750Z'),
 edge('starts-next','2099-01-01T00:00:00.500Z','invalid'),
 edge('navigation','2099-01-01T00:00:00.050Z','',purpose='navigation'),
 edge('reference','2099-01-01T00:00:00.075Z','',purpose='reference')]
a.synchronize(root,source)
p=a._home(root)/'sync.json';data=p.read_bytes()
for now,boundary,active in [(4070908800,4070908800.5,{'invalid','ends-next'}),
                            (4070908800.5,4070908800.75,{'invalid','ends-next','starts-next'}),
                            (4070908800.75,0,{'invalid','starts-next'}),
                            (4070908801,0,{'invalid','starts-next'})]:
 with patch.object(a.time,'time',return_value=now):
  view=a.read_graph(root,read_only=True)
  assert view['next_transition_at']==0
  assert not [e for e in view['edges'] if e.get('purpose')=='semantic']
 assert p.read_bytes()==data
print(json.dumps({'ignoredInvalidPastWithdrawn':True,'nextSemanticBoundary':True}))
''')
        self.assertEqual(result, {'ignoredInvalidPastWithdrawn': True, 'nextSemanticBoundary': True})


    def test_dictionary_topic_is_archived_with_incoming_links_and_raw_preserved(self):
        result = self.execute(r'''
a.synchronize(root,source);contract,graph,secrets,write=a._load_engine(root)
home=a._home(root);p=home/'sync.json';state=json.loads(p.read_text())
old={'id':'topic:stocks','label':'사전 주제','category':'topic','description':'자동 분류','facts':[]}
title=a._title(old,secrets);receipt=write.create_node(title,'자동 분류','사전 분류 내용','agent',space=a.SOURCE_SPACE)
path=graph.Index().by_id[receipt['id']][0];original=path.read_bytes();sha=a.digest(original)
state['managed']['topic:stocks']={'osk_id':receipt['id'],'title':title,'space':a.SOURCE_SPACE,'written_hash':sha,'source':old,'active':True}
item=state['managed']['memory:one'];note_path=graph.Index().by_id[item['osk_id']][0]
body=contract.parse(note_path).body+'\n\n사용자 문장 [['+title+'|분류 이름]] 보존.'
write.update_node(item['osk_id'],body=body,expect_hash=a.digest(note_path.read_bytes()))
item['written_hash']=a.digest(note_path.read_bytes());a._save(p,state)
raw=home/'vault/_sources/raw-proof.txt';raw.parent.mkdir(exist_ok=True);raw.write_bytes(b'## source-proof\n\nIMMUTABLE RAW')
write.update_node(item['osk_id'],add_edges={'derived-from':['[[_sources/raw-proof.txt#source-proof]]']})
item['written_hash']=a.digest(note_path.read_bytes());a._save(p,state)
source['nodes'].append(old);a.synchronize(root,source)
state=json.loads(p.read_text());assert state['managed']['topic:stocks']['withdrawn'] and not path.exists()
assert (home/'vault/_archive/alden-preclassification-v1'/(sha+'.bin')).read_bytes()==original
assert raw.read_bytes()==b'## source-proof\n\nIMMUTABLE RAW'
assert '사용자 문장 분류 이름 보존.' in contract.parse(note_path).body
assert not a.read_focus(root,'topic:stocks')['ok'] and not a.read_focus(root,'osk:'+receipt['id'])['ok']
assert 'topic:stocks' not in {n['id'] for n in a.read_graph(root)['nodes']}
assert json.loads((home/'canonical-migration.json').read_text())['state']=='complete'
assert graph.layout_violations()==[]
ledger=list((home/'vault/00_Scope/Workbench/_ledger/migration').glob('events.jsonl'))[0]
assert any(json.loads(line)['kind']=='archive' for line in ledger.read_text().splitlines())
counts=len(ledger.read_text().splitlines());a.synchronize(root,source)
assert not path.exists() and len(ledger.read_text().splitlines())==counts
print(json.dumps({'withdrawn':True,'rawPreserved':True,'idempotent':True}))
''')
        self.assertEqual(result, {'withdrawn':True,'rawPreserved':True,'idempotent':True})

    def test_legacy_markdown_backups_recover_as_opaque_bytes_before_early_return(self):
        result = self.execute(r'''
a.synchronize(root,source);contract,graph,_,write=a._load_engine(root)
home=a._home(root);state=json.loads((home/'sync.json').read_text())
item=state['managed']['memory:one'];path=graph.Index().by_id[item['osk_id']][0]
original=path.read_bytes();sha=a.digest(original)
archive=home/'vault/_archive/alden-preclassification-v1';archive.mkdir(parents=True,exist_ok=True)
old=archive/(sha+'.md');old.write_bytes(original)
assert graph.layout_violations()
a.synchronize(root,source)
assert not old.exists() and (archive/(sha+'.bin')).read_bytes()==original
assert path.read_bytes()==original and graph.layout_violations()==[]
receipt=json.loads((home/'canonical-archive-format-migration.json').read_text())
assert receipt['state']=='complete' and len(receipt['moves'])==1
ledger=home/'vault/00_Scope/Workbench/_ledger/migration/events.jsonl'
before=ledger.read_bytes();a.synchronize(root,source)
assert ledger.read_bytes()==before
print(json.dumps({'opaque':True,'bytePreserved':True,'idempotent':True}))
''')
        self.assertEqual(result, {'opaque':True,'bytePreserved':True,'idempotent':True})

    def test_only_explicit_osk_dependencies_enter_graph_with_direction(self):
        result = self.execute(r'''
a.synchronize(root,source);contract,graph,_,write=a._load_engine(root)
state=json.loads((a._home(root)/'sync.json').read_text());one=state['managed']['memory:one'];room=state['managed']['chat:1']
p=graph.Index().by_id[one['osk_id']][0]
write.update_node(one['osk_id'],body='독립 작성 내용 [['+room['osk_id']+']]',expect_hash=a.digest(p.read_bytes()),add_edges={'derived-from':[room['osk_id']]})
view=a.read_graph(root);dependencies=[e for e in view['edges'] if e.get('purpose')!='navigation']
assert {(e['source'],e['relation'],e['target']) for e in dependencies}=={('memory:one','linked','chat:1'),('memory:one','derived-from','chat:1')}
assert all(e['evidence']['osk_id']==one['osk_id'] for e in dependencies)
assert not any(e['relation']=='mentions' for e in view['edges'])
print(json.dumps({'direction':True,'canonical':True}))
''')
        self.assertEqual(result, {'direction':True,'canonical':True})

    def test_human_corrected_dictionary_topic_is_held_and_never_overwritten(self):
        result = self.execute(r'''
a.synchronize(root,source);contract,graph,secrets,write=a._load_engine(root)
home=a._home(root);p=home/'sync.json';state=json.loads(p.read_text())
node={'id':'topic:old','label':'사전 주제','category':'topic'}
receipt=write.create_node('보존할 수정','사용자 수정','사람이 작성한 내용','agent',space=a.SOURCE_SPACE)
path=graph.Index().by_id[receipt['id']][0];before=path.read_bytes()
state['managed']['topic:old']={'osk_id':receipt['id'],'title':path.stem,'space':a.SOURCE_SPACE,'written_hash':a.digest(before),'source':node,'active':True,'human_corrected':True}
a._save(p,state);a.synchronize(root,source)
after=json.loads(p.read_text());assert path.read_bytes()==before and not after['managed']['topic:old'].get('withdrawn')
assert 'topic:old' in after['canonical_migration']['held'] and not a.read_focus(root,'osk:'+receipt['id'])['ok']
print(json.dumps({'held':True,'humanBytesPreserved':True}))
''')
        self.assertEqual(result, {'held':True,'humanBytesPreserved':True})



    def test_graph_snapshot_refreshes_authoritative_body_links_after_each_edit(self):
        result = self.execute(r'''a.synchronize(root,source);contract,graph,_,write=a._load_engine(root)
p=a._home(root)/'sync.json';state=json.loads(p.read_text());one=state['managed']['memory:one'];room=state['managed']['chat:1']
note=graph.Index().by_id[one['osk_id']][0]
assert not [e for e in a.read_graph(root,read_only=True)['edges'] if e.get('purpose')=='reference']
write.update_node(one['osk_id'],body='현재 노트 [['+room['osk_id']+']]',summary='첫 수정',expect_hash=a.digest(note.read_bytes()))
before=note.read_bytes();view=a.read_graph(root,read_only=True)
assert note.read_bytes()==before and any(e['source']=='memory:one' and e['target']=='chat:1' and e['relation']=='linked' for e in view['edges'])
write.update_node(one['osk_id'],body='참조를 철회한 현재 노트',summary='둘째 수정',expect_hash=a.digest(note.read_bytes()))
view=a.read_graph(root,read_only=True)
assert not [e for e in view['edges'] if e.get('purpose')=='reference']
assert next(n for n in view['nodes'] if n['id']=='memory:one')['description']=='둘째 수정'
print(json.dumps({'freshAfterEdit':True,'freshAfterRetraction':True}))
''')
        self.assertEqual(result, {'freshAfterEdit':True,'freshAfterRetraction':True})


if __name__ == "__main__":
    unittest.main()
