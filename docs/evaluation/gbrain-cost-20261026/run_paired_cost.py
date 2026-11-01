"""Three warm paired whole-pool refresh comparisons; no product/source writes."""
from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,os,platform,re,sqlite3,subprocess,sys,time

BASE=Path(__file__).resolve().parent;OUTPUTS=BASE.parent
RESUME=os.environ.get('ALDEN_COST_RESUME')=='1'
REPO=OUTPUTS.parent/'work/alden-explicit-references-20261025';SCRIPTS=REPO/'scripts'
sys.path.insert(0,str(SCRIPTS))
from alden_collection import CollectionStore
from alden_gbrain_import import FullGbrainCache,FullProjection
ORIGINAL=OUTPUTS/'graph-export-20261025T190849Z/source-public-state'
REVIEWED=OUTPUTS/'gbrain-upgrade-20261025T154408Z'
SOURCE=BASE/'source';SCOPE=['export-miracl-ko'];CACHES={mode:BASE/(mode+'-cache') for mode in ('full','delta')}
env={k:os.environ[k] for k in ('PATH','LANG','LC_ALL','TZ','HOME','TMPDIR') if k in os.environ}
env.update(PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=str(SCRIPTS))
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def manifest(root):return {str(p.relative_to(root)):sha(p) for p in sorted(root.rglob('*')) if p.is_file() and not p.name.endswith(('-wal','-shm'))}
def write(name,value):(BASE/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
NAMES=('alden_collection.py','alden_collection_export.py','alden_collection_delta.py','alden_gbrain_import.py',
 'alden_gbrain_delta.py','alden_gbrain_cache.py','alden_gbrain_client.py','alden_knowledge_mcp.py')
PINS={n:sha(SCRIPTS/n) for n in NAMES}
def pin():assert PINS=={n:sha(SCRIPTS/n) for n in NAMES}
original=manifest(ORIGINAL)
assert original==json.loads((ORIGINAL.parent/'source-public-input-binding.json').read_text())['public_fixture_before']
if RESUME:
 assert SOURCE.is_dir()
 assert json.loads((BASE/'input-binding.json').read_text())['module_pins']==PINS
else:
 assert not SOURCE.exists();subprocess.run(['/bin/cp','-cR',str(ORIGINAL),str(SOURCE)],check=True);SOURCE.chmod(0o700)
 assert manifest(SOURCE)==original
 write('input-binding.json',{'head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=REPO,text=True).strip(),
  'module_pins':PINS,'original_manifest':original,'source':str(SOURCE),'scope':SCOPE,
  'device':{'platform':platform.platform(),'python':sys.version,'cpus':os.cpu_count(),
     'memory_bytes':int(subprocess.check_output(['/usr/sbin/sysctl','-n','hw.memsize'],text=True)),
     'power':subprocess.check_output(['/usr/bin/pmset','-g','batt'],text=True).splitlines()[0]},
  'mode':'warm paired full-source/index/engine rebuild versus incremental; same source/edit and stable format',
  'monitor':'wait4 accounted CPU + native maxrss; /bin/ps sampled sum RSS of owned command tree every0.25s, shared pages double counted',
  'cold_cache_flush':False,'model_embedding_calls':0,'app_launches':0,'external_changes_to_original':False})

entry=BASE/'instrumented_entry.py'
entry.write_text('''from pathlib import Path
import json,os,sys,time
import alden_gbrain_import as full
import alden_collection_delta as delta
import alden_gbrain_delta as engine
trace={}
def timed(name,callback,*args,**kwargs):
 start=time.perf_counter()
 try:return callback(*args,**kwargs)
 finally:
  row=trace.setdefault(name,{'calls':0,'inclusive_seconds':0});row['calls']+=1;row['inclusive_seconds']+=time.perf_counter()-start
def wrap(owner,attribute,name):
 original=getattr(owner,attribute)
 setattr(owner,attribute,lambda *a,**k:timed(name,original,*a,**k))
wrap(full,'export_graph','full_source_export')
wrap(full,'verify_current_export','current_source_verification')
wrap(delta,'export_incremental','source_delta_export')
wrap(delta,'_matches','delta_full_source_comparison')
wrap(engine,'clone_closed','closed_engine_copy')
wrap(engine,'build_delta','engine_delta_build_including_verification')
wrap(full.FullGbrainCache,'_build','full_engine_build_including_verification')
wrap(full.FullGbrainCache,'_verify','complete_engine_readback')
original=full.FullProjection.prepare.__func__
full.FullProjection.prepare=classmethod(lambda cls,*a,**k:timed('index_preparation',original,cls,*a,**k))
path=Path(os.environ['ALDEN_COST_TRACE'])
try:full.main()
finally:path.write_text(json.dumps({'phases':trace,'timing':'inclusive, nested spans are not additive'}))
''')

records=[];events=json.loads((BASE/'progress.json').read_text())['events'] if RESUME else []
def progress(phase,**extra):
 value={'phase':phase,'utc':datetime.now(timezone.utc).isoformat(),**extra};events.append(value)
 write('progress.json',{'finished':False,'events':events});print(json.dumps(value),flush=True)
def tree(pid):
 raw=subprocess.check_output(['/bin/ps','-axo','pid=,ppid=,rss=,comm='],text=True)
 rows={}
 for line in raw.splitlines():
  cols=line.strip().split(None,3)
  if len(cols)==4:rows[int(cols[0])]=(int(cols[1]),int(cols[2]))
 owned={pid}
 while True:
  more={p for p,(parent,_) in rows.items() if parent in owned}-owned
  if not more:break
  owned|=more
 return sum(rows[p][1] for p in owned if p in rows),len(owned&rows.keys())
def measured(name,args,extra_env=None):
 pin();folder=BASE/'calls'/name;folder.mkdir(parents=True,mode=0o700)
 command=[sys.executable,'-B',*map(str,args)];samples=[];started=time.perf_counter()
 record={'name':name,'argv':command,'state':'running','started_utc':datetime.now(timezone.utc).isoformat(),
    'load_average':list(os.getloadavg()),'source_modules':PINS}
 with (folder/'stdout').open('xb') as out,(folder/'stderr').open('xb') as err:
  child=subprocess.Popen(command,cwd=BASE,env={**env,**(extra_env or {})},stdout=out,stderr=err,start_new_session=True)
  record['pid']=child.pid;write('calls/'+name+'/process.json',record)
  while True:
   ended,status,usage=os.wait4(child.pid,os.WNOHANG)
   if ended:
    child.returncode=os.waitstatus_to_exitcode(status);break
   rss,count=tree(child.pid);samples.append([time.perf_counter()-started,rss,count]);time.sleep(.25)
 elapsed=time.perf_counter()-started;pin()
 record.update(state='terminal',exit_code=child.returncode,wall_seconds=elapsed,
    wait4_user_seconds=usage.ru_utime,wait4_system_seconds=usage.ru_stime,
    wait4_cpu_seconds=usage.ru_utime+usage.ru_stime,wait4_maxrss_native_units=usage.ru_maxrss,
    sampled_sum_rss_peak_kib=max((r[1] for r in samples),default=0),sample_count=len(samples),
    peak_tree_processes=max((r[2] for r in samples),default=0))
 write('calls/'+name+'/process.json',record);write('calls/'+name+'/rss-samples.json',samples)
 assert child.returncode==0,(name,record,(folder/'stderr').read_text()[-1200:],(folder/'stdout').read_text()[-1200:])
 records.append(record);return record

# Confirm the platform's accounted-child CPU behavior using an owned nested
# CPU loop. Raw maxrss remains labeled in native units without inferred units.
calibration=BASE/'calibration.py'
calibration.write_text("import subprocess,sys\nsubprocess.run([sys.executable,'-c','import time; end=time.process_time()+0.4\\nwhile time.process_time()<end: pass'],check=True)\n")
if not RESUME:
 cal=measured('monitor-calibration',[calibration]);assert cal['wait4_cpu_seconds']>.3 and cal['sample_count']>0
 write('monitor-calibration.json',{'nested_child_busy_cpu_target_seconds':.4,'observed_wait4_cpu_seconds':cal['wait4_cpu_seconds'],
  'wait4_accounts_nested_child_in_this_calibration':True,'sampled_tree_processes':cal['peak_tree_processes'],
  'remaining_limits':'sampling misses peaks between observations; RSS sum includes shared pages; root rusage is platform-accounted, not hardware energy'})

def cli(name,mode,action,*options):
 trace=BASE/('trace-'+name+'.json')
 record=measured(name,[entry,action,'--root',CACHES[mode],'--state-root',SOURCE,'--project',SCOPE[0],*options],
    {'ALDEN_COST_TRACE':str(trace)})
 result=json.loads((BASE/'calls'/name/'stdout').read_text());assert result.get('ok') is True,result
 record['result']=result;record['phases']=json.loads(trace.read_text())['phases'];write('calls/'+name+'/measured.json',record)
 progress(name,seconds=record['wall_seconds'],cpu=record['wait4_cpu_seconds'],peak_rss_kib=record['sampled_sum_rss_peak_kib'])
 return record
def current(mode):
 cache=FullGbrainCache(CACHES[mode],collection_root=SOURCE,projects=SCOPE)
 return FullProjection.open(cache._generation(cache.active()['generation']))
def logical(projection):
 h=hashlib.sha256()
 with sqlite3.connect(projection.path.as_uri()+'?mode=ro',uri=True) as db:
  for table,columns,order in [('pages','slug,id,kind,payload,content','slug'),('links','a,b,verb,kind,payload,context','a,b,verb')]:
   for row in db.execute('SELECT '+columns+' FROM '+table+' ORDER BY '+order):h.update(json.dumps([table,*row],ensure_ascii=False,separators=(',',':')).encode()+b'\n')
 return h.hexdigest()
def revisions(mode):return {p['slug']:p['revision'] for p in current(mode).pages()}

if not RESUME:
 for mode in ('full','delta'):
  cli('create-'+mode,mode,'create','--runtime-root',REVIEWED,'--runtime-home',REVIEWED/'gbrain-home','--runtime-receipt',REVIEWED/'runtime-receipt.json')
  cli('initial-'+mode,mode,'refresh','--incremental')
assert current('full').fingerprint==current('delta').fingerprint
assert current('full').counts()['pages']==3049 and current('full').counts()['links']==6105

store=CollectionStore(SOURCE)
with store.database() as db:
 chosen=dict(db.execute('''SELECT m.target_id,d.original_id,v.label,v.body,v.metadata FROM memberships m
 JOIN documents d ON d.id=m.document_id JOIN versions v ON v.id=m.current_version ORDER BY d.id LIMIT 1''').fetchone())
base_label=re.sub(r' \[paired cost \d+\]$','',chosen['label'])
rounds=[]
for number,order in enumerate((('full','delta'),('delta','full'),('full','delta')),1):
 before={mode:revisions(mode) for mode in CACHES}
 write('before-round-'+str(number)+'-revisions.json',before)
 record=json.loads(chosen['metadata']);record.update(original_id=chosen['original_id'],label=base_label+' [paired cost '+str(number)+']',text=chosen['body'])
 if not (RESUME and number==1):
  edit=store.ingest(chosen['target_id'],[record],origin='paired-cost-owned-copy');assert edit['state']=='complete'
 else:assert chosen['label']==record['label']
 modes={}
 for mode in order:
  modes[mode]=cli('round-'+str(number)+'-'+mode+('-valid' if RESUME and number==1 and mode=='full' else ''),mode,'refresh',*([] if mode=='full' else ['--incremental']))
  result=modes[mode]['result'];assert result['pages']==3049 and result['links']==6105 and result['verified_queries']==3049
 a,b=current('full'),current('delta');assert a.fingerprint==b.fingerprint and logical(a)==logical(b)
 retained={mode:sum(rev==before[mode].get(slug) for slug,rev in revisions(mode).items()) for mode in CACHES}
 assert retained['delta']==3048
 ready=json.loads((b.root/'ready.json').read_text());assert ready['update_mode']=='engine_delta' and ready['source_update']['mode']=='journal_delta'
 row={'round':number,'order':list(order),'measurements':modes,'fingerprint':a.fingerprint,'logical_index_sha256':logical(a),
    'equal_complete_projection':True,'retained_revisions':retained,'delta_receipt':ready}
 rounds.append(row);write('paired-rounds.json',rounds)
 progress('paired-round-'+str(number)+'-verified',retained=retained)

for mode in ('full','delta'):cli('withdraw-'+mode,mode,'withdraw')
assert manifest(ORIGINAL)==original
pin();write('acceptance.json',{'head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=REPO,text=True).strip(),
 'source_modules':PINS,'original_manifest_exact':True,'nodes':3048,'pages':3049,'links':6105,
 'rounds':rounds,'measurement':'three warm paired rounds, same source/edit, alternating order; instrumented wrappers only time original functions',
 'complete_projection_equality':True,'full_engine_verification_queries_per_trial':3049,'sample_period_seconds':.25,
 'limits':['not a cold-start/GUI/GPU/native-account/model workload','three pairs do not establish p95/asymptotic scaling or general production gains',
 'sum RSS double counts shared pages and sampled peaks can miss brief peaks','active protected background workloads retained; no workload suspension',
 'only own copied source/derived stores changed; no user root or service/connection enrollment']})
write('progress.json',{'finished':True,'events':events});print(json.dumps({'finished':True,'pairs':len(rounds),'original_exact':True}),flush=True)
