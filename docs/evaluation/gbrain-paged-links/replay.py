"""Owned synthetic star replay against the reviewed pinned engine; no GUI or original-source changes."""
from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,os,sys,time
BASE=Path(os.environ['ALDEN_PAGED_OUTPUT']).resolve()
BASE.mkdir(mode=0o700)
call_number=0
SCRIPTS=Path(os.environ.get('ALDEN_PAGED_SCRIPTS',str(Path(__file__).resolve().parents[3]/'scripts'))).resolve();sys.path.insert(0,str(SCRIPTS))
from alden_collection import CollectionStore,identity
from alden_gbrain_import import FullGbrainCache,FullProjection,GbrainImportClient,FullGbrainReader
from alden_gbrain_client import GbrainReadClient,GbrainError,SOURCE_COMMIT
REVIEWED=Path(os.environ['ALDEN_PAGED_RUNTIME']).resolve()
def write(name,value):(BASE/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
names=['alden_gbrain_client.py','alden_gbrain_import.py','alden_gbrain_cache.py','alden_gbrain_delta.py','alden_collection.py','alden_collection_export.py','alden_collection_delta.py']
pins={n:hashlib.sha256((SCRIPTS/n).read_bytes()).hexdigest() for n in names}
source=BASE/'source';source.mkdir(mode=0o700);store=CollectionStore(source)
target=store.register(platform='graph',original_id='explicit-star',kind='source',label='Synthetic boundary test',projects=['paged-boundary'])
cache=FullGbrainCache.create(BASE/'cache',collection_root=source,projects=['paged-boundary'],
 runtime_root=REVIEWED,runtime_home=REVIEWED/'gbrain-home',runtime_receipt=REVIEWED/'runtime-receipt.json')
def record(i):return {'original_id':'hub' if i<0 else 'leaf-'+str(i),'label':'Explicit hub' if i<0 else 'Explicit leaf '+str(i),'text':'owned synthetic boundary fixture'}
def relation(i):return {'source_platform':'graph','source_id':'hub','target_platform':'graph','target_id':'leaf-'+str(i),'type':'reference','evidence':{'fixture':'explicit directed relation'}}
def add(start,end):
 records=([record(-1)] if start==0 else [])+[record(i) for i in range(start,end)]
 for offset in range(0,len(records),1000):store.ingest(target,records[offset:offset+1000])
 for offset in range(start,end,1000):store.ingest(target,[],relations=[relation(i) for i in range(offset,min(end,offset+1000))])
def inspect(expected,result):
 projection=FullProjection.open(cache._generation(cache.active()['generation']))
 assert result['paged_outgoing_pages']==1 and result['paged_outgoing_links']==expected,result
 assert result['pages']==expected+1 and result['links']==expected*2-1,result
 with cache.lease(exclusive=False) as fd:
  with GbrainReadClient(cache.root/'runtime-receipt.json',home=projection.root/'home',owned_root=cache.root,lease_fd=fd) as client:
   global call_number
   original=client.transport.request
   def traced(method,params):
    global call_number
    result=original(method,params)
    if method=='tools/call' and params.get('name')=='get_links':
     call_number+=1
     write('last-link-response.json',{'call':call_number,'arguments':params['arguments'],'response':result})
     with (BASE/'notice-trace.jsonl').open('a') as log:
      log.write(json.dumps({'call':call_number,'notices':result.get('_meta',{}).get('gbrain_notices',[]),'block_headers':[p.get('text','').split('\n')[0][:200] for p in result.get('content',[])[1:]]})+'\n')
    return result
   client.transport.request=traced
   slug=projection.slug(identity('graph','hub'))
   accepted=projection.verify_outgoing(slug,client.outgoing_links(slug,max_rows=expected,page_limit=317))
   assert accepted==expected
   try:client.graph(slug,depth=1,direction='out')
   except GbrainError as error:
    bounded_error=str(error);assert bounded_error in ('gbrain_graph_truncated','gbrain_graph_unavailable'),bounded_error
   else:raise AssertionError('bounded traversal unexpectedly accepted full high-degree graph')
  # Independent second read session: publisher-selected smaller pages and complete page/revision proof.
  with GbrainImportClient(cache.root/'runtime-receipt.json',home=projection.root/'home',owned_root=cache.root,lease_fd=fd) as client:
   assert projection.verify_outgoing(slug,client.outgoing_links(slug,max_rows=expected,page_limit=499))==expected
 return {'outgoing':accepted,'full_refresh':result,'independent_page_limits':[317,499],
  'bounded_traversal':bounded_error,'fingerprint':projection.fingerprint}
trials=[]
matrix=[('full-5000',0,4999,False),('delta-9436',4999,9435,False),('full-9436',9435,9435,True)]
if os.environ.get('ALDEN_PAGED_SINGLE')=='9436':matrix=[('full-9436',0,9435,True)]
try:
 for name,start,end,rebuild in matrix:
  print(json.dumps({'phase':name,'state':'running','utc':datetime.now(timezone.utc).isoformat()}),flush=True)
  if end>start:add(start,end)
  began=time.monotonic();result=cache.refresh(incremental=True,rebuild=rebuild)
  write(name+'-refresh.json',result)
  accepted=inspect(end+1,result);accepted.update(name=name,elapsed_seconds=time.monotonic()-began)
  trials.append(accepted);write('trials.json',trials)
  print(json.dumps({'phase':name,'state':'verified','pages':result['pages'],'links':result['links']}),flush=True)
 for trial in trials:
  assert trial['full_refresh']['update_mode']==('engine_delta' if trial['name'].startswith('delta-') else 'full_rebuild')
finally:
 with cache.lease(exclusive=True):removed=cache._retire(None)
 write('cleanup.json',{'active':cache.active(),'remaining_generations':len(list(cache.generations.iterdir())),'removed':removed})
assert not cache.active() and not list(cache.generations.iterdir())
assert pins=={n:hashlib.sha256((SCRIPTS/n).read_bytes()).hexdigest() for n in names}
write('acceptance.json',{'publisher_source_commit':SOURCE_COMMIT,'python_module_root':str(SCRIPTS),'module_pins':pins,
 'fixture':'explicit synthetic; no original production/private account reads','trials':trials,'remaining_generations':0,
 'gui_launches':0,'installation':False,'source_or_config_changes':False,'full_goal':'active',
 'limits':['bounded graph requests still reject provider cap','high-degree contexts above2000UTF16units remain explicit rejection',
 'not installed UI/native account/semantic gold/external review proof']})
print(json.dumps({'state':'accepted','trials':len(trials)}),flush=True)

